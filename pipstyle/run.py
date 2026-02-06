#!/usr/bin/env python3
"""
Run DB-backed resolution for every node in a subgraph (one root bit).

Loads context from MongoDB, streams subgraph nodes from the given subgraph + root bit,
runs runner.resolve() for each node, and writes results to CSV plus optional tree files.

Usage:
  python -m pipstyle.run --subgraph urllib3_subgraph --root-bit-index 82 --output-dir output [--debug]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from typing import Any, Optional, Set, Tuple

try:
    from pymongo import MongoClient
    _HAS_PYMONGO = True
except ImportError:
    _HAS_PYMONGO = False

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, desc=None, **kwargs):
        return iterable

from packaging.utils import canonicalize_name

from pipstyle import load_context, ResolutionRunner
from pipstyle.loader import ResolutionContext
from pipstyle.resolvelib.resolvers.exceptions import ResolverException


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Run DB-backed resolution for all nodes in a subgraph (one root bit)."
    )
    ap.add_argument("--mongo-uri", default="mongodb://localhost:27017", help="MongoDB connection URI")
    ap.add_argument("--pypi-db", default="pypi_dump", help="Database name for PyPI collections")
    ap.add_argument("--subgraphs-db", default="subgraphs", help="Database name for subgraph collections")

    ap.add_argument("--subgraph", required=True, help="Subgraph collection name (e.g. urllib3_subgraph)")
    ap.add_argument(
        "--root-bit-index",
        type=int,
        default=None,
        help="Root version bit index (0..nbits-1). Default: latest (nbits-1).",
    )
    ap.add_argument("--mask-field", default="roots_bits", help="Field used for bit filter on edges")
    ap.add_argument("--meta-coll", default=None, help="Meta collection name (default: <subgraph>__meta)")
    ap.add_argument("--subgraph-batch-size", type=int, default=100_000, help="Batch size when streaming subgraph edges")

    ap.add_argument("--output-dir", default="output", help="Output directory for CSV and optional tree subdir")
    ap.add_argument("--chunk-cache-cap", type=int, default=200_000, help="LRU cap for chunk cache")
    ap.add_argument("--header-cache-cap", type=int, default=500_000, help="LRU cap for header cache")
    ap.add_argument("--debug", action="store_true", help="Store resolved dependency trees per node")

    return ap.parse_args()


def load_root_from_meta(
    meta_coll: Any,
    subgraph_name: str,
    root_bit_index: Optional[int],
) -> Tuple[str, str, int, int, int]:
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
        bit_index = len(root_versions) - 1
    else:
        bit_index = int(root_bit_index)

    if bit_index < 0 or bit_index >= len(root_versions):
        raise RuntimeError(
            f"--root-bit-index out of range: {bit_index}, valid: 0..{len(root_versions) - 1}"
        )

    root_ver = str(root_versions[bit_index])
    root_id = int(root_ids[bit_index])
    return str(root_pkg), root_ver, bit_index, root_id, nbits


def collect_subgraph_nodes_for_bit(
    subgraph_coll: Any,
    bit_index: int,
    mask_field: str,
    batch_size: int,
) -> Set[int]:
    """Collect unique node_ids in the subgraph for the given root bit by streaming edges."""
    q = {mask_field: {"$bitsAllSet": [bit_index]}}
    proj = {"src_id": 1, "dst_id": 1}
    nodes: Set[int] = set()
    cur = subgraph_coll.find(q, proj, no_cursor_timeout=True).batch_size(batch_size)
    try:
        for e in tqdm(cur, desc=f"Stream subgraph edges (bit={bit_index})"):
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


def run() -> None:
    args = parse_args()
    if not _HAS_PYMONGO:
        raise RuntimeError("pymongo is required. Install with: pip install pymongo")

    client = MongoClient(args.mongo_uri)
    pypi_db = client[args.pypi_db]
    sub_db = client[args.subgraphs_db]

    subgraph_name = args.subgraph
    meta_name = args.meta_coll or f"{subgraph_name}__meta"
    meta_coll = sub_db[meta_name]
    subgraph_coll = sub_db[subgraph_name]

    root_pkg, root_ver, bit_index, root_id, nbits = load_root_from_meta(
        meta_coll, subgraph_name, args.root_bit_index
    )
    root_pkg_canon = canonicalize_name(root_pkg)
    print(f"[root] pkg={root_pkg} ver={root_ver} bit_index={bit_index} root_id={root_id} nbits={nbits}")

    print("[load] Loading resolution context from MongoDB ...")
    ctx = load_context(
        mongo_uri=args.mongo_uri,
        pypi_db=args.pypi_db,
        chunk_cache_cap=args.chunk_cache_cap,
        header_cache_cap=args.header_cache_cap,
    )
    name_to_id = {v: k for k, v in ctx.name_id_to_name.items()}
    root_name_id = name_to_id.get(root_pkg_canon)
    if root_name_id is None:
        raise RuntimeError(f"Root package {root_pkg_canon!r} not found in name_ids.")
    print(f"[root] root_name_id={root_name_id}")

    if root_id >= len(ctx.node_time) or ctx.node_time[root_id] is None:
        raise RuntimeError("Root node has no timestamp; cannot proceed.")
    root_time = int(ctx.node_time[root_id])

    print("[subgraph] Collecting nodes ...")
    nodes = collect_subgraph_nodes_for_bit(
        subgraph_coll, bit_index, args.mask_field, args.subgraph_batch_size
    )
    node_list = sorted(nodes)
    print(f"[subgraph] {len(node_list):,} nodes for bit {bit_index}")

    os.makedirs(args.output_dir, exist_ok=True)
    csv_name = f"{subgraph_name}_{bit_index}.csv"
    csv_path = os.path.join(args.output_dir, csv_name)

    trees_dir: Optional[str] = None
    if args.debug:
        trees_dir = os.path.join(args.output_dir, f"{subgraph_name}_{bit_index}_resolved_trees")
        os.makedirs(trees_dir, exist_ok=True)
        print(f"[debug] Resolved trees will be written to {trees_dir!r}")

    runner = ResolutionRunner(ctx)

    num_resolved = 0
    num_resolved_reached = 0   # resolved and depth >= 0 (root in dep tree)
    num_resolved_not_reached = 0  # resolved and depth == -1
    num_not_resolved = 0

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["node_id", "resolved", "depth"])

        for node_id in tqdm(node_list, desc="Resolve"):
            nt = ctx.node_time[node_id] if node_id < len(ctx.node_time) else None
            if nt is None:
                writer.writerow([node_id, False, ""])
                num_not_resolved += 1
                continue

            t_cutoff = max(int(nt), root_time)
            try:
                resolved, depth, tree = runner.resolve(
                    node_id=node_id,
                    root_node_id=root_id,
                    root_name_id=root_name_id,
                    time=t_cutoff,
                    debug=args.debug,
                )
            except ResolverException:
                # Handle resolver exceptions (InconsistentCandidate, ResolutionImpossible, etc.)
                # These occur when there are self-dependencies, circular dependencies, or other inconsistencies
                # Treat as not resolved
                resolved = False
                depth = -1
                tree = None

            writer.writerow([node_id, resolved, depth if depth >= 0 else ""])

            if resolved:
                num_resolved += 1
                if depth >= 0:
                    num_resolved_reached += 1
                else:
                    num_resolved_not_reached += 1
                if args.debug and tree is not None:
                    tree_path = os.path.join(trees_dir, f"{node_id}.json")
                    with open(tree_path, "w") as tf:
                        json.dump(tree, tf, indent=0)
            else:
                num_not_resolved += 1

    print(f"[output] Wrote {csv_path}")

    print("\n--- Final stats ---")
    print(f"  Total nodes processed:     {len(node_list):,}")
    print(f"  Resolved:                  {num_resolved:,}")
    print(f"  Resolved + reached in dep tree (depth >= 0): {num_resolved_reached:,}")
    print(f"  Resolved + not reached in dep tree (depth -1): {num_resolved_not_reached:,}")
    print(f"  Not resolved:              {num_not_resolved:,}")


if __name__ == "__main__":
    run()
