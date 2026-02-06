"""
Load in-memory collections from MongoDB and provide LRU-backed chunk access.

All collections except global_graph_adj_chunks are loaded into memory at startup.
Chunks are loaded on demand and cached with an LRU (configurable size).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Optional: only needed when loading from MongoDB
try:
    from pymongo import MongoClient
    from pymongo.database import Database
    from pymongo.collection import Collection
    _HAS_PYMONGO = True
except ImportError:
    _HAS_PYMONGO = False
    MongoClient = None  # type: ignore
    Database = None  # type: ignore
    Collection = None  # type: ignore


def _epoch_from_dt(x: Any) -> Optional[int]:
    """Convert BSON datetime to epoch seconds. Return None if missing."""
    if x is None:
        return None
    try:
        return int(x.timestamp())
    except Exception:
        return None


class LRUCache:
    """Simple LRU cache for (src_id, dep_name_id, chunk) -> list of dst_ids."""

    def __init__(self, cap: int):
        self.cap = max(0, int(cap))
        self._od: OrderedDict = OrderedDict()

    def get(self, k: Tuple[int, int, int]) -> Optional[List[int]]:
        if self.cap <= 0:
            return None
        if k in self._od:
            self._od.move_to_end(k)
            return self._od[k]
        return None

    def put(self, k: Tuple[int, int, int], v: List[int]) -> None:
        if self.cap <= 0:
            return
        if k in self._od:
            self._od.move_to_end(k)
            self._od[k] = v
            return
        self._od[k] = v
        while len(self._od) > self.cap:
            self._od.popitem(last=False)

    def __len__(self) -> int:
        return len(self._od)


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
    min_t: Optional[int]
    max_t: Optional[int]


@dataclass
class ResolutionContext:
    """
    In-memory context for resolution: arrays indexed by node_id and lookup maps.
    Chunks are fetched via get_chunk() and cached in chunk_lru.
    """

    # Arrays indexed by node_id (length = max_node_id + 1)
    node_py_mask: List[int]
    node_time: List[Optional[int]]
    node_name_id: List[Optional[int]]

    # name_id -> name (and optionally name -> name_id)
    name_id_to_name: Dict[int, str] = field(default_factory=dict)

    # src_id -> list of dep_name_id (direct dependencies)
    adj_deps: Dict[int, List[int]] = field(default_factory=dict)

    # (src_id, dep_name_id) -> DepHeader
    adj_headers: Dict[Tuple[int, int], Optional[DepHeader]] = field(default_factory=dict)

    # LRU cache for chunk data; key (src_id, dep_name_id, chunk), value list of dst_ids
    chunk_lru: Optional[LRUCache] = None

    # For on-demand chunk loading (when using MongoDB)
    chunks_coll: Any = None  # pymongo Collection or None if using preloaded data only

    def get_chunk(self, src_id: int, dep_name_id: int, chunk: int) -> List[int]:
        """Return dst_ids for the given chunk (from cache or DB)."""
        key = (src_id, dep_name_id, chunk)
        if self.chunk_lru is not None:
            cached = self.chunk_lru.get(key)
            if cached is not None:
                return cached
        if self.chunks_coll is not None:
            doc = self.chunks_coll.find_one(
                {"src_id": src_id, "dep_name_id": dep_name_id, "chunk": chunk},
                {"dst_ids": 1},
            )
            dst_ids = [int(x) for x in (doc.get("dst_ids") or [])] if doc else []
            if self.chunk_lru is not None:
                self.chunk_lru.put(key, dst_ids)
            return dst_ids
        return []

    def get_dep_name_ids(self, src_id: int) -> List[int]:
        """Return list of dep_name_id for src_id."""
        return self.adj_deps.get(src_id, [])

    def get_header(self, src_id: int, dep_name_id: int) -> Optional[DepHeader]:
        """Return DepHeader for (src_id, dep_name_id)."""
        return self.adj_headers.get((src_id, dep_name_id))


def load_context(
    mongo_uri: str = "mongodb://localhost:27017",
    pypi_db: str = "pypi_dump",
    chunk_cache_cap: int = 200_000,
) -> ResolutionContext:
    """
    Load all in-memory collections from MongoDB and create resolution context.
    Requires pymongo. Chunks are loaded on demand and cached.
    """
    if not _HAS_PYMONGO:
        raise RuntimeError("pymongo is required for load_context()")
    client = MongoClient(mongo_uri)
    db = client[pypi_db]

    node_ids_coll = db["global_graph_node_ids"]
    name_ids_coll = db["global_graph_name_ids"]
    rp_coll = db["global_graph_requires_python_with_timestamps"]
    adj_deps_coll = db["global_graph_adj_deps"]
    adj_headers_coll = db["global_graph_adj_headers"]
    chunks_coll = db["global_graph_adj_chunks"]

    # 1) Build name_id <-> name
    name_id_to_name: Dict[int, str] = {}
    for d in name_ids_coll.find({}, {"name": 1, "id": 1}):
        n = d.get("name")
        i = d.get("id")
        if n is not None and i is not None:
            name_id_to_name[int(i)] = str(n)

    # 2) Find max node_id and build node_id -> name_id from global_graph_node_ids
    max_id = 0
    for d in node_ids_coll.find({}, {"id": 1, "name": 1}).batch_size(50000):
        nid = d.get("id")
        if nid is not None:
            nid = int(nid)
            if nid > max_id:
                max_id = nid

    name_to_id = {v: k for k, v in name_id_to_name.items()}
    node_name_id: List[Optional[int]] = [None] * (max_id + 1)
    for d in node_ids_coll.find({}, {"id": 1, "name": 1}).batch_size(50000):
        nid = d.get("id")
        name = d.get("name")
        if nid is not None and name is not None:
            nid = int(nid)
            name_id = name_to_id.get(str(name))
            if 0 <= nid <= max_id and name_id is not None:
                node_name_id[nid] = name_id

    # 3) requires_python_with_timestamps -> node_py_mask, node_time
    all_mask = 0
    for d in rp_coll.find({}, {"_id": 1, "py_mask": 1}).batch_size(100000):
        nid = int(d["_id"])
        if nid > max_id:
            max_id = nid
        pm = d.get("py_mask")
        if pm is not None:
            all_mask |= int(pm)
    if all_mask == 0:
        all_mask = (1 << 26) - 1

    # Extend arrays if needed
    while len(node_name_id) <= max_id:
        node_name_id.append(None)

    node_py_mask = [all_mask] * (max_id + 1)
    node_time = [None] * (max_id + 1)
    for d in rp_coll.find({}, {"_id": 1, "py_mask": 1, "first_upload_time": 1}).batch_size(100000):
        nid = int(d["_id"])
        if nid <= max_id:
            pm = d.get("py_mask")
            if pm is not None:
                node_py_mask[nid] = int(pm)
            node_time[nid] = _epoch_from_dt(d.get("first_upload_time"))

    # 4) adj_deps: src_id -> list of dep_name_id
    adj_deps: Dict[int, List[int]] = {}
    for d in adj_deps_coll.find({}, {"_id": 1, "deps": 1}).batch_size(50000):
        src_id = d.get("_id")
        deps = d.get("deps") or []
        if src_id is not None:
            adj_deps[int(src_id)] = [int(x) for x in deps]

    # 5) adj_headers: (src_id, dep_name_id) -> DepHeader
    adj_headers: Dict[Tuple[int, int], Optional[DepHeader]] = {}
    for doc in adj_headers_coll.find({}).batch_size(20000):
        src_id = doc.get("src_id")
        dep_name_id = doc.get("dep_name_id")
        if src_id is None or dep_name_id is None:
            continue
        src_id = int(src_id)
        dep_name_id = int(dep_name_id)
        mi = doc.get("mi") or []
        ma = doc.get("ma") or []
        nn = doc.get("n") or []
        if not (isinstance(mi, list) and isinstance(ma, list) and isinstance(nn, list)):
            adj_headers[(src_id, dep_name_id)] = None
            continue
        L = len(nn)
        if len(mi) != L or len(ma) != L:
            adj_headers[(src_id, dep_name_id)] = None
            continue
        chunks_list: List[ChunkInfo] = []
        overall_min = None
        overall_max = None
        for idx in range(L):
            min_t = int(mi[idx]) if mi[idx] is not None else None
            max_t = int(ma[idx]) if ma[idx] is not None else None
            cnt = int(nn[idx]) if nn[idx] is not None else 0
            chunks_list.append(ChunkInfo(chunk=idx, n=cnt, min_t=min_t, max_t=max_t))
            if min_t is not None:
                overall_min = min_t if overall_min is None else min(overall_min, min_t)
            if max_t is not None:
                overall_max = max_t if overall_max is None else max(overall_max, max_t)
        adj_headers[(src_id, dep_name_id)] = DepHeader(
            src_id=src_id,
            dep_name_id=dep_name_id,
            chunks=chunks_list,
            min_t=overall_min,
            max_t=overall_max,
        )

    chunk_lru = LRUCache(chunk_cache_cap)
    return ResolutionContext(
        node_py_mask=node_py_mask,
        node_time=node_time,
        node_name_id=node_name_id,
        name_id_to_name=name_id_to_name,
        adj_deps=adj_deps,
        adj_headers=adj_headers,
        chunk_lru=chunk_lru,
        chunks_coll=chunks_coll,
    )
