#!/usr/bin/env python3
"""
Phase 4 (CSP-correct): Node exposure scan on a chosen root-version subgraph
(time-aware forward DFS / backtracking with GLOBAL consistency).

Input:
  --subgraph urllib3_subgraph   (collection in DB 'subgraphs')
Uses:
  subgraphs.<subgraph> and subgraphs.<subgraph>__meta
  pypi_dump.global_graph_adj_headers
  pypi_dump.global_graph_adj_chunks
  pypi_dump.global_graph_requires_python_with_timestamps
  pypi_dump.global_graph_name_ids
  pypi_dump.global_graph_node_ids   (to map node_id -> package name_id)

Output CSV:
  node_id, node_time_epoch, t_cutoff_epoch, exposed, depth_to_root

Semantics (what you want):
  exposure(n, root_id, t) is TRUE iff there exists a globally consistent assignment
  of ONE version per package name encountered such that:
    - start node is fixed to node_id=n
    - root package is forced to EXACT root_id when required (and must be required somewhere)
    - for every chosen node, upload_time <= t
    - python masks intersect non-zero across ALL chosen nodes
    - every dependency edge is satisfied (parent version allows the chosen child version)
"""

import argparse
import csv
import time
from collections import OrderedDict, Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Iterator, Set, Any

from pymongo import MongoClient
from packaging.utils import canonicalize_name
from packaging.version import Version

from tqdm import tqdm


# ----------------------------
# Small utilities
# ----------------------------

def epoch_from_dt_maybe(x) -> Optional[int]:
    """Convert BSON datetime to epoch seconds. Return None if missing/unparseable."""
    if x is None:
        return None
    try:
        return int(x.timestamp())
    except Exception:
        return None


# ----------------------------
# LRU caches (Cache #1)
# ----------------------------

class LRUCache:
    """Simple LRU for arbitrary key->value."""
    def __init__(self, cap: int):
        self.cap = int(cap)
        self.od: OrderedDict = OrderedDict()

    def get(self, k):
        if self.cap <= 0:
            return None
        if k in self.od:
            self.od.move_to_end(k)
            return self.od[k]
        return None

    def put(self, k, v):
        if self.cap <= 0:
            return
        if k in self.od:
            self.od.move_to_end(k)
            self.od[k] = v
            return
        self.od[k] = v
        if len(self.od) > self.cap:
            self.od.popitem(last=False)

    def __len__(self):
        return len(self.od)


# ----------------------------
# Adjacency access via headers/chunks
# ----------------------------

@dataclass
class ChunkInfo:
    chunk: int
    n: int
    min_t: Optional[int]
    max_t: Optional[int]


@dataclass
class DepHeader:
    src_id: int
    dep_name_id: int
    chunks: List[ChunkInfo]
    min_t: Optional[int]  # overall
    max_t: Optional[int]  # overall


class AdjStore:
    """
    Fetch deps for src via global_graph_adj_headers,
    and candidate dst_ids via global_graph_adj_chunks.
    Uses LRU caching for:
      - deps list per src_id
      - header per (src_id, dep_name_id)
      - chunk docs per (src_id, dep_name_id, chunk)
    """
    def __init__(
        self,
        headers_coll,
        chunks_coll,
        node_time: List[Optional[int]],
        deps_cache_cap: int = 200_000,
        header_cache_cap: int = 200_000,
        chunk_cache_cap: int = 200_000,
        edgecheck_cache_cap: int = 2_000_000,
    ):
        self.headers = headers_coll
        self.chunks = chunks_coll
        self.node_time = node_time

        self.deps_lru = LRUCache(deps_cache_cap)
        self.header_lru = LRUCache(header_cache_cap)
        self.chunk_lru = LRUCache(chunk_cache_cap)

        # Optional: cache edge-existence checks keyed by (src_id, dep_name_id, dst_id, t_bucket)
        # to avoid repeated scans when a dep is already globally chosen.
        self.edge_lru = LRUCache(edgecheck_cache_cap)

    def get_dep_name_ids(self, src_id: int) -> List[int]:
        """Returns all dep_name_id that src depends on. Cached by src_id."""
        cached = self.deps_lru.get(src_id)
        if cached is not None:
            return cached

        cur = self.headers.find({"src_id": src_id}, {"dep_name_id": 1})
        dep_ids = [int(d["dep_name_id"]) for d in cur]
        self.deps_lru.put(src_id, dep_ids)
        return dep_ids

    def get_header(self, src_id: int, dep_name_id: int) -> Optional[DepHeader]:
        """
        Reads one header doc from global_graph_adj_headers:

        {
            _id: {src_id, dep_name_id},
            src_id: <int>,
            dep_name_id: <int>,
            mi: [<int>...],   # per-chunk min upload_time (epoch seconds)
            ma: [<int>...],   # per-chunk max upload_time (epoch seconds)
            n:  [<int>...],   # per-chunk count of dst_ids in that chunk
            total: <int>      # sum(n)
        }

        Interprets chunk indices as 0..len(n)-1.
        """
        k = (src_id, dep_name_id)
        cached = self.header_lru.get(k)
        if cached is not None:
            return cached

        doc = self.headers.find_one(
            {"src_id": src_id, "dep_name_id": dep_name_id},
            {"src_id": 1, "dep_name_id": 1, "mi": 1, "ma": 1, "n": 1, "total": 1},
        )
        if not doc:
            self.header_lru.put(k, None)
            return None

        mi = doc.get("mi") or []
        ma = doc.get("ma") or []
        nn = doc.get("n") or []

        # Defensive: require consistent lengths
        if not (isinstance(mi, list) and isinstance(ma, list) and isinstance(nn, list)):
            self.header_lru.put(k, None)
            return None

        L = len(nn)
        if len(mi) != L or len(ma) != L:
            # If inconsistent, be conservative: treat as missing
            self.header_lru.put(k, None)
            return None

        chunks: List[ChunkInfo] = []
        overall_min = None
        overall_max = None

        for idx in range(L):
            min_t = mi[idx]
            max_t = ma[idx]
            cnt = nn[idx]

            # Convert to ints (or None)
            min_t_i = int(min_t) if min_t is not None else None
            max_t_i = int(max_t) if max_t is not None else None
            cnt_i = int(cnt) if cnt is not None else 0

            chunks.append(ChunkInfo(chunk=idx, n=cnt_i, min_t=min_t_i, max_t=max_t_i))

            if min_t_i is not None:
                overall_min = min_t_i if overall_min is None else min(overall_min, min_t_i)
            if max_t_i is not None:
                overall_max = max_t_i if overall_max is None else max(overall_max, max_t_i)

        h = DepHeader(
            src_id=int(doc["src_id"]),
            dep_name_id=int(doc["dep_name_id"]),
            chunks=chunks,
            min_t=overall_min,
            max_t=overall_max,
        )
        self.header_lru.put(k, h)
        return h


    def get_chunk_dst_ids(self, src_id: int, dep_name_id: int, chunk: int) -> List[int]:
        k = (src_id, dep_name_id, chunk)
        cached = self.chunk_lru.get(k)
        if cached is not None:
            return cached

        doc = self.chunks.find_one(
            {"src_id": src_id, "dep_name_id": dep_name_id, "chunk": chunk},
            {"dst_ids": 1},
        )
        if not doc:
            self.chunk_lru.put(k, [])
            return []
        dst_ids = [int(x) for x in (doc.get("dst_ids") or [])]
        self.chunk_lru.put(k, dst_ids)
        return dst_ids

    def _bisect_right_by_time(self, dst_ids: List[int], t: int) -> int:
        """
        dst_ids sorted by time ascending.
        Return i such that dst_ids[:i] have time <= t.
        Missing time treated as invalid (excluded).
        """
        lo, hi = 0, len(dst_ids)
        while lo < hi:
            mid = (lo + hi) // 2
            nid = dst_ids[mid]
            tm = self.node_time[nid] if nid < len(self.node_time) else None
            if tm is None or tm > t:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def iter_candidates_newest_first(
        self,
        src_id: int,
        dep_name_id: int,
        t: int,
        max_candidates: int = 0,
    ) -> Iterator[int]:
        """
        Yield dst node_ids for (src_id, dep_name_id) with node_time <= t,
        newest-first. Uses chunk min/max time ranges + bisect in last chunk.
        """
        h = self.get_header(src_id, dep_name_id)
        if h is None or not h.chunks:
            return iter(())

        if h.min_t is not None and h.min_t > t:
            return iter(())

        yielded = 0

        for ci in reversed(h.chunks):
            if ci.min_t is not None and ci.min_t > t:
                continue

            dst_ids = self.get_chunk_dst_ids(src_id, dep_name_id, ci.chunk)
            if not dst_ids:
                continue

            if ci.max_t is not None and ci.max_t <= t:
                cut = len(dst_ids)
            else:
                cut = self._bisect_right_by_time(dst_ids, t)

            for i in range(cut - 1, -1, -1):
                nid = dst_ids[i]
                tm = self.node_time[nid] if nid < len(self.node_time) else None
                if tm is None or tm > t:
                    continue
                yield nid
                yielded += 1
                if max_candidates and yielded >= max_candidates:
                    return

        return

    def edge_exists_upto_t(self, src_id: int, dep_name_id: int, dst_id: int, t: int) -> bool:
        """
        True iff dst_id is among candidates for (src_id, dep_name_id) with time <= t.
        Uses header/chunks, scans eligible prefixes.
        Cached with a coarse time bucket to avoid worst repeated rescans.
        """
        # coarse bucket to increase cache hits; tune if needed
        t_bucket = t // (24 * 3600)  # day bucket
        key = (src_id, dep_name_id, dst_id, t_bucket)
        hit = self.edge_lru.get(key)
        if hit is not None:
            return bool(hit)

        h = self.get_header(src_id, dep_name_id)
        if h is None or not h.chunks:
            self.edge_lru.put(key, False)
            return False
        if h.min_t is not None and h.min_t > t:
            self.edge_lru.put(key, False)
            return False

        for ci in h.chunks:
            if ci.min_t is not None and ci.min_t > t:
                break

            dst_ids = self.get_chunk_dst_ids(src_id, dep_name_id, ci.chunk)
            if not dst_ids:
                continue

            if ci.max_t is not None and ci.max_t <= t:
                cut = len(dst_ids)
            else:
                cut = self._bisect_right_by_time(dst_ids, t)

            # scan eligible prefix
            for i in range(cut):
                if dst_ids[i] == dst_id:
                    self.edge_lru.put(key, True)
                    return True

        self.edge_lru.put(key, False)
        return False


# ----------------------------
# Exposure solver (CSP-correct backtracking)
# ----------------------------

@dataclass
class SolveResult:
    ok: bool
    depth_to_root: Optional[int]
    fail_reason: str = ""


class ExposureSolverCSP:
    """
    Global-consistency solver:
      chosen[name_id] = node_id, one version per package name globally.
    """
    def __init__(
        self,
        adj: AdjStore,
        node_py_mask: List[int],
        node_time: List[Optional[int]],
        node_name_id: List[Optional[int]],
        all_mask: int,
        root_id: int,
        root_name_id: int,
        start_name_id: Optional[int],
        max_candidates_per_dep: int = 0,
        debug: bool = False,
        trace_node: Optional[int] = None,
        trace_limit: int = 2000,
    ):
        self.adj = adj
        self.node_py_mask = node_py_mask
        self.node_time = node_time
        self.node_name_id = node_name_id
        self.all_mask = all_mask
        self.root_id = int(root_id)
        self.root_name_id = int(root_name_id)
        self.max_candidates_per_dep = int(max_candidates_per_dep)

        self.debug = bool(debug)
        self.trace_node = trace_node
        self.trace_limit = int(trace_limit)
        self._trace_lines = 0

        # these are set per exposure() call
        self._fail_ctr: Counter = Counter()

    def _pmask(self, nid: int) -> int:
        if nid < len(self.node_py_mask):
            return int(self.node_py_mask[nid])
        return int(self.all_mask)

    def _ntime(self, nid: int) -> Optional[int]:
        if nid < len(self.node_time):
            return self.node_time[nid]
        return None

    def _nname(self, nid: int) -> Optional[int]:
        if nid < len(self.node_name_id):
            return self.node_name_id[nid]
        return None

    def _trace(self, msg: str):
        if not self.debug:
            return
        if self._trace_lines >= self.trace_limit:
            return
        print(msg)
        self._trace_lines += 1

    def exposure(self, start_id: int, t: int) -> SolveResult:
        """
        Returns ok, and depth_to_root (min depth along some satisfying assignment).
        Also provides a fail_reason when ok=False (coarse).
        """
        self._fail_ctr = Counter()
        self._trace_lines = 0

        start_id = int(start_id)
        if start_id == self.root_id:
            return SolveResult(True, 0, "")

        tm0 = self._ntime(start_id)
        if tm0 is None:
            self._fail_ctr["start_time_missing"] += 1
            return SolveResult(False, None, "start_time_missing")
        if tm0 > t:
            self._fail_ctr["start_after_t"] += 1
            return SolveResult(False, None, "start_after_t")

        m0 = self._pmask(start_id)
        if m0 == 0:
            self._fail_ctr["start_pymask_zero"] += 1
            return SolveResult(False, None, "start_pymask_zero")

        start_name_id = self._nname(start_id)
        if start_name_id is None:
            # conservative: if we can't identify name, cannot enforce global consistency
            self._fail_ctr["start_name_missing"] += 1
            return SolveResult(False, None, "start_name_missing")

        # Global state for CSP
        chosen: Dict[int, int] = {}
        in_stack: Set[int] = set()

        # Pin start package name to this exact version node_id
        chosen[start_name_id] = start_id

        # Pin root package name to root_id (but "root_required" must become True for success)
        chosen[self.root_name_id] = self.root_id

        # Global python intersection across chosen vars
        allowed_py = m0 & self._pmask(self.root_id)
        if allowed_py == 0:
            self._fail_ctr["root_pymask_conflict_at_start"] += 1
            return SolveResult(False, None, "root_pymask_conflict_at_start")

        in_stack.add(start_id)

        root_required = False
        best_depth: Optional[int] = None

        ok = self._solve_node(
            node_id=start_id,
            t=t,
            chosen=chosen,
            allowed_py=allowed_py,
            in_stack=in_stack,
            depth_from_start=0,
            root_required_ref=[root_required],  # boxed bool
            best_depth_ref=[best_depth],        # boxed Optional[int]
        )

        root_required = bool(ok and self._last_root_required)
        best_depth = self._last_best_depth

        if ok and root_required and best_depth is not None:
            return SolveResult(True, best_depth, "")

        # choose a representative reason
        reason = "unsat"
        if self._fail_ctr:
            reason = self._fail_ctr.most_common(1)[0][0]
        return SolveResult(False, None, reason)

    def _solve_node(
        self,
        node_id: int,
        t: int,
        chosen: Dict[int, int],
        allowed_py: int,
        in_stack: Set[int],
        depth_from_start: int,
        root_required_ref: List[bool],
        best_depth_ref: List[Optional[int]],
    ) -> bool:
        """
        Ensure node_id's dependencies are satisfiable under global `chosen`.
        Returns True if satisfiable and root_required becomes True somewhere in closure.
        Updates best_depth_ref with minimal depth-to-root discovered.
        """
        # store back out for exposure()
        self._last_root_required = root_required_ref[0]
        self._last_best_depth = best_depth_ref[0]

        dep_name_ids = self.adj.get_dep_name_ids(node_id)

        # If this node has no outgoing deps, it is satisfiable, but may not force root
        if not dep_name_ids:
            return True

        # We'll backtrack over dep_name_ids in order
        dep_ids = dep_name_ids

        def bt(i: int, allowed_py_local: int) -> bool:
            # persist for exposure()
            self._last_root_required = root_required_ref[0]
            self._last_best_depth = best_depth_ref[0]

            if i == len(dep_ids):
                return True

            dep_name_id = dep_ids[i]

            # Mark that this assignment requires root if we see root package as a dependency
            if dep_name_id == self.root_name_id:
                root_required_ref[0] = True
                self._last_root_required = True

            # If dep already globally chosen, validate edge and recurse into that chosen node
            if dep_name_id in chosen:
                dst_id = chosen[dep_name_id]

                # must exist by time t
                tm = self._ntime(dst_id)
                if tm is None or tm > t:
                    self._fail_ctr["chosen_dst_time_invalid"] += 1
                    return False

                # must be reachable via an edge from this node version to that chosen version
                if not self.adj.edge_exists_upto_t(node_id, dep_name_id, dst_id, t):
                    self._fail_ctr["edge_missing_for_chosen"] += 1
                    return False

                # python compatibility
                new_allowed = allowed_py_local & self._pmask(dst_id)
                if new_allowed == 0:
                    self._fail_ctr["python_conflict_with_chosen"] += 1
                    return False

                if dst_id in in_stack:
                    # cycle is okay (already assigned), treat as satisfied
                    return bt(i + 1, new_allowed)

                in_stack.add(dst_id)
                ok_child = self._solve_node(
                    node_id=dst_id,
                    t=t,
                    chosen=chosen,
                    allowed_py=new_allowed,
                    in_stack=in_stack,
                    depth_from_start=depth_from_start + 1,
                    root_required_ref=root_required_ref,
                    best_depth_ref=best_depth_ref,
                )
                in_stack.remove(dst_id)

                if not ok_child:
                    self._fail_ctr["child_unsat_with_chosen"] += 1
                    return False

                # depth accounting if this chosen is root_id
                if dst_id == self.root_id:
                    d = depth_from_start + 1
                    bd = best_depth_ref[0]
                    if bd is None or d < bd:
                        best_depth_ref[0] = d
                        self._last_best_depth = d

                return bt(i + 1, new_allowed)

            # Otherwise, choose a candidate version for this dependency package
            # Root forcing: if dep is root package, ONLY candidate is root_id
            if dep_name_id == self.root_name_id:
                cand_iter = iter([self.root_id])
            else:
                cand_iter = self.adj.iter_candidates_newest_first(
                    node_id, dep_name_id, t, max_candidates=self.max_candidates_per_dep
                )

            any_tried = False

            for dst_id in cand_iter:
                any_tried = True
                dst_id = int(dst_id)

                # existence <= t (defensive; iterator should ensure)
                tm = self._ntime(dst_id)
                if tm is None or tm > t:
                    continue

                # python
                new_allowed = allowed_py_local & self._pmask(dst_id)
                if new_allowed == 0:
                    continue

                # cycle check
                if dst_id in in_stack:
                    continue

                # Commit global choice for this package name
                chosen[dep_name_id] = dst_id
                in_stack.add(dst_id)

                # Recurse into dst
                ok_child = self._solve_node(
                    node_id=dst_id,
                    t=t,
                    chosen=chosen,
                    allowed_py=new_allowed,
                    in_stack=in_stack,
                    depth_from_start=depth_from_start + 1,
                    root_required_ref=root_required_ref,
                    best_depth_ref=best_depth_ref,
                )

                in_stack.remove(dst_id)

                if ok_child:
                    # depth if root
                    if dst_id == self.root_id:
                        d = depth_from_start + 1
                        bd = best_depth_ref[0]
                        if bd is None or d < bd:
                            best_depth_ref[0] = d
                            self._last_best_depth = d

                    # Now satisfy remaining deps at this node
                    if bt(i + 1, new_allowed):
                        return True

                # Backtrack global choice
                chosen.pop(dep_name_id, None)

            if not any_tried:
                self._fail_ctr["no_candidates_for_dep"] += 1
            else:
                self._fail_ctr["all_candidates_failed_for_dep"] += 1
            return False

        return bt(0, allowed_py)


# ----------------------------
# Main
# ----------------------------

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    ap.add_argument("--pypi-db", default="pypi_dump")
    ap.add_argument("--subgraphs-db", default="subgraphs")

    ap.add_argument("--subgraph", required=True, help="e.g. urllib3_subgraph")
    ap.add_argument("--mask-field", default="roots_bits")
    ap.add_argument("--meta-coll", default=None, help="defaults to <subgraph>__meta")

    # Allow selecting root bit explicitly
    ap.add_argument(
        "--root-bit-index",
        type=int,
        default=None,
        help="If provided, use this bit index (0..nbits-1). Else defaults to latest (nbits-1).",
    )

    ap.add_argument("--adj-headers-coll", default="global_graph_adj_headers")
    ap.add_argument("--adj-chunks-coll", default="global_graph_adj_chunks")

    ap.add_argument("--rp-coll", default="global_graph_requires_python_with_timestamps")
    ap.add_argument("--nameids-coll", default="global_graph_name_ids")
    ap.add_argument("--nodeids-coll", default="global_graph_node_ids")

    ap.add_argument("--out-csv", default=None)

    # caches
    ap.add_argument("--deps-cache-cap", type=int, default=200_000)
    ap.add_argument("--header-cache-cap", type=int, default=200_000)
    ap.add_argument("--chunk-cache-cap", type=int, default=200_000)
    ap.add_argument("--edgecheck-cache-cap", type=int, default=2_000_000)

    # solver knobs
    ap.add_argument("--max-candidates-per-dep", type=int, default=0,
                    help="0 = no limit; else try only newest K candidates per dependency.")
    ap.add_argument("--subgraph-batch-size", type=int, default=100_000)
    ap.add_argument("--progress-every", type=int, default=50_000)

    # debug
    ap.add_argument("--debug", action="store_true", help="Enable debug reason counters + optional tracing.")
    ap.add_argument("--debug-trace-node", type=int, default=None,
                    help="If set, prints a limited trace only when processing this node_id.")
    ap.add_argument("--debug-trace-limit", type=int, default=2000,
                    help="Max trace lines printed for the traced node.")
    return ap.parse_args()


def load_root_from_meta(meta_coll, subgraph_name: str, root_bit_index: Optional[int]) -> Tuple[str, str, int, int, int]:
    """
    Returns: (root_pkg, root_ver, bit_index, root_id, nbits)
    """
    doc = meta_coll.find_one({})
    if not doc:
        raise RuntimeError(f"Meta collection for {subgraph_name} is empty.")

    root_pkg = doc.get("pkg")
    root_versions = doc.get("root_versions") or []
    root_ids = doc.get("root_ids") or []
    nbits = int(doc.get("nbits", len(root_versions)))

    if not root_pkg or not root_versions or not root_ids or len(root_versions) != len(root_ids):
        raise RuntimeError("Bad meta doc: missing pkg/root_versions/root_ids or length mismatch.")

    if root_bit_index is None:
        bit_index = len(root_versions) - 1  # "latest" in the meta ordering
    else:
        bit_index = int(root_bit_index)

    if bit_index < 0 or bit_index >= len(root_versions):
        raise RuntimeError(f"--root-bit-index out of range: {bit_index}, valid: 0..{len(root_versions)-1}")

    root_ver = str(root_versions[bit_index])
    root_id = int(root_ids[bit_index])

    return str(root_pkg), root_ver, bit_index, root_id, nbits


def load_name_to_id(nameids_coll) -> Dict[str, int]:
    """global_graph_name_ids docs: { name: <canonical>, id: <int> }"""
    m: Dict[str, int] = {}
    cur = nameids_coll.find({}, {"name": 1, "id": 1}).batch_size(50_000)
    for d in cur:
        nm = d.get("name")
        i = d.get("id")
        if nm is None or i is None:
            continue
        m[str(nm)] = int(i)
    return m


def load_node_masks_and_times(rp_coll) -> Tuple[List[int], List[Optional[int]], int]:
    """
    rp docs:
      { _id: <node_id>, py_mask: <int>, first_upload_time: <datetime or None> }
    Returns:
      node_py_mask: list indexed by node_id (missing => ALL_MASK)
      node_time: list indexed by node_id (missing => None)
      all_mask: int computed as bitwise OR over observed masks (fallback if empty)
    """
    max_id = 0
    all_mask = 0

    cur = rp_coll.find({}, {"_id": 1, "py_mask": 1}).batch_size(100_000)
    for d in cur:
        nid = int(d["_id"])
        if nid > max_id:
            max_id = nid
        pm = d.get("py_mask")
        if pm is not None:
            all_mask |= int(pm)

    if all_mask == 0:
        all_mask = (1 << 26) - 1

    node_py_mask = [all_mask] * (max_id + 1)
    node_time: List[Optional[int]] = [None] * (max_id + 1)

    cur2 = rp_coll.find({}, {"_id": 1, "py_mask": 1, "first_upload_time": 1}).batch_size(100_000)
    for d in cur2:
        nid = int(d["_id"])
        pm = d.get("py_mask")
        if pm is not None:
            node_py_mask[nid] = int(pm)
        node_time[nid] = epoch_from_dt_maybe(d.get("first_upload_time"))

    return node_py_mask, node_time, all_mask


def load_nodeid_to_nameid(nodeids_coll, name_to_id: Dict[str, int], max_node_id: int) -> List[Optional[int]]:
    """
    Build node_id -> name_id array using global_graph_node_ids:
      { name: <canonical>, version: <str>, id: <int node_id> }
    We only need name_id, so we map name via name_to_id.
    """
    arr: List[Optional[int]] = [None] * (max_node_id + 1)
    cur = nodeids_coll.find({}, {"id": 1, "name": 1}).batch_size(200_000)
    for d in tqdm(cur, desc="Load node_id -> name_id"):
        nid = d.get("id")
        nm = d.get("name")
        if nid is None or nm is None:
            continue
        nid = int(nid)
        if 0 <= nid <= max_node_id:
            name_id = name_to_id.get(str(nm))
            if name_id is not None:
                arr[nid] = int(name_id)
    return arr


def collect_subgraph_nodes_for_bit(subgraph_coll, bit_index: int, mask_field: str, batch_size: int) -> Set[int]:
    """
    Collect unique node_ids in a root-version subgraph by streaming edges.
    Edge docs: { src_id, dst_id, roots_bits }
    """
    q = {mask_field: {"$bitsAllSet": [bit_index]}}
    proj = {"src_id": 1, "dst_id": 1}
    nodes: Set[int] = set()

    cur = subgraph_coll.find(q, proj, no_cursor_timeout=True).batch_size(batch_size)
    try:
        for e in tqdm(cur, desc=f"Stream subgraph edges (bit={bit_index}) collect nodes"):
            s = e.get("src_id")
            d = e.get("dst_id")
            if s is not None:
                nodes.add(int(s))
            if d is not None:
                nodes.add(int(d))
    finally:
        try:
            cur.close()
        except Exception:
            pass

    return nodes


def main():
    args = parse_args()

    client = MongoClient(args.mongo_uri)

    pypi_db = client[args.pypi_db]
    sub_db = client[args.subgraphs_db]

    sg = sub_db[args.subgraph]
    meta_name = args.meta_coll or f"{args.subgraph}__meta"
    meta = sub_db[meta_name]

    headers = pypi_db[args.adj_headers_coll]
    chunks = pypi_db[args.adj_chunks_coll]
    rp = pypi_db[args.rp_coll]
    nameids = pypi_db[args.nameids_coll]
    nodeids = pypi_db[args.nodeids_coll]

    root_pkg, root_ver, bit_index, root_id, nbits = load_root_from_meta(
        meta, args.subgraph, args.root_bit_index
    )
    root_pkg_canon = canonicalize_name(root_pkg)

    print(f"[root] pkg={root_pkg} canon={root_pkg_canon} ver={root_ver} bit_index={bit_index} root_id={root_id} nbits={nbits}")

    print("[load] loading name->id map ...")
    t0 = time.time()
    name_to_id = load_name_to_id(nameids)
    t1 = time.time()
    print(f"[load] name->id size={len(name_to_id):,} time={t1-t0:.1f}s")

    if root_pkg_canon not in name_to_id:
        raise RuntimeError(f"Root canonical name {root_pkg_canon!r} not found in global_graph_name_ids.")
    root_name_id = int(name_to_id[root_pkg_canon])
    print(f"[root] root_name_id={root_name_id}")

    print("[load] loading node py_masks + upload times ...")
    t2 = time.time()
    node_py_mask, node_time, all_mask = load_node_masks_and_times(rp)
    t3 = time.time()
    print(f"[load] node arrays size={len(node_py_mask):,} time={t3-t2:.1f}s all_mask={all_mask}")

    # Root time must exist
    if root_id >= len(node_time) or node_time[root_id] is None:
        raise RuntimeError("Root node has no timestamp in requires_python_with_timestamps; cannot proceed.")
    root_t = int(node_time[root_id])
    print(f"[root] root_upload_time_epoch={root_t}")

    # Load node_id -> name_id (required for start pinning and true global consistency)
    print("[load] building node_id -> name_id array (from global_graph_node_ids) ...")
    node_name_id = load_nodeid_to_nameid(nodeids, name_to_id, max_node_id=len(node_py_mask) - 1)
    print("[load] node_id -> name_id loaded.")

    # Collect nodes in this root-bit subgraph
    nodes = collect_subgraph_nodes_for_bit(sg, bit_index, args.mask_field, args.subgraph_batch_size)
    print(f"[subgraph] unique nodes collected for bit[{bit_index}]: {len(nodes):,}")

    out_csv = args.out_csv or f"{args.subgraph}__bit{bit_index}_nodes_exposure.csv"
    print(f"[out] writing to {out_csv}")

    adj = AdjStore(
        headers_coll=headers,
        chunks_coll=chunks,
        node_time=node_time,
        deps_cache_cap=args.deps_cache_cap,
        header_cache_cap=args.header_cache_cap,
        chunk_cache_cap=args.chunk_cache_cap,
        edgecheck_cache_cap=args.edgecheck_cache_cap,
    )

    node_list = list(nodes)
    node_list.sort()

    # Debug aggregation
    reason_ctr = Counter()
    exposed_ct = 0
    tested = 0
    t_start = time.time()

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node_id", "node_time_epoch", "t_cutoff_epoch", "exposed", "depth_to_root", "fail_reason"])

        for nid in tqdm(node_list, desc=f"Exposure per node (bit={bit_index})"):
            tested += 1

            nt = node_time[nid] if nid < len(node_time) else None
            if nt is None:
                w.writerow([nid, None, None, 0, "", "node_time_missing"])
                reason_ctr["node_time_missing"] += 1
                continue

            t_cutoff = max(int(nt), root_t)

            do_trace = args.debug and (args.debug_trace_node is not None) and (nid == args.debug_trace_node)
            solver = ExposureSolverCSP(
                adj=adj,
                node_py_mask=node_py_mask,
                node_time=node_time,
                node_name_id=node_name_id,
                all_mask=all_mask,
                root_id=root_id,
                root_name_id=root_name_id,
                start_name_id=node_name_id[nid] if nid < len(node_name_id) else None,
                max_candidates_per_dep=args.max_candidates_per_dep,
                debug=do_trace,
                trace_node=args.debug_trace_node,
                trace_limit=args.debug_trace_limit,
            )

            res = solver.exposure(nid, t_cutoff)

            if res.ok:
                exposed_ct += 1
                w.writerow([nid, int(nt), t_cutoff, 1, res.depth_to_root if res.depth_to_root is not None else "", ""])
            else:
                w.writerow([nid, int(nt), t_cutoff, 0, "", res.fail_reason])
                reason_ctr[res.fail_reason or "unsat"] += 1

            if args.progress_every and tested % args.progress_every == 0:
                elapsed = time.time() - t_start
                rate = tested / max(elapsed, 1e-9)
                print(
                    f"[prog] tested={tested:,} exposed={exposed_ct:,} "
                    f"rate={rate:,.2f} nodes/s "
                    f"deps_cache={len(adj.deps_lru):,} header_cache={len(adj.header_lru):,} "
                    f"chunk_cache={len(adj.chunk_lru):,} edge_cache={len(adj.edge_lru):,}"
                )
                if args.debug and reason_ctr:
                    top = reason_ctr.most_common(10)
                    print("[debug] top fail reasons:", top)

    print("Done.")
    if args.debug and reason_ctr:
        print("[debug] final fail reasons (top 30):")
        for k, v in reason_ctr.most_common(30):
            print(f"  {k:30s} {v:,}")


if __name__ == "__main__":
    main()

"""
Examples:

# Default: latest bit (nbits-1)
python3 phase4_exposure_nodes.py --subgraph urllib3_subgraph --debug

# Explicit root version bit index (0..nbits-1)
python3 phase4_exposure_nodes_1.py --subgraph urllib3_subgraph --root-bit-index 82 --debug

pyxdg,0.20,3,5922,1052 ->
python3 phase4_exposure_nodes_1.py --subgraph pyxdg_subgraph --root-bit-index 1 --debug --progress-every 5

# Trace a single node (prints limited trace only for that node)
python3 phase4_exposure_nodes.py --subgraph urllib3_subgraph --root-bit-index 82 \
  --debug --debug-trace-node 2248381 --debug-trace-limit 5000

python3 phase4_exposure_nodes_1.py \
  --mongo-uri mongodb://localhost:27017 \
  --pypi-db pypi_dump \
  --subgraphs-db subgraphs \
  --subgraph urllib3_subgraph \
  --mask-field roots_bits \
  --out-csv urllib3_latest_nodes_exposure.csv \
  --deps-cache-cap 200000 \
  --header-cache-cap 200000 \
  --chunk-cache-cap 200000 \
  --max-candidates-per-dep 0 \
  --progress-every 5000

"""
