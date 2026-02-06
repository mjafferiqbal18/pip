# pipstyle: DB-Backed Dependency Resolution

This package implements **dependency resolution** using preprocessed MongoDB collections instead of querying PyPI. It reuses pip's **resolvelib** (resolution and backtracking logic) and plugs in a **DB-backed provider** that reads candidates, dependencies, and Python compatibility from your existing graph collections.

## What it does

- **Resolves** a given package version (`node_id`) against the dependency graph, with a **pinned root** (`root_node_id` for `root_name_id`) and a **time cutoff** so only versions that exist at or before that time are considered.
- **Outputs**: whether a consistent assignment exists (`resolved`), the **depth** (minimum hops from the resolved node to the root in the dependency tree), and optionally the **dependency tree** (for debugging).
- **Guarantees**: one version per package name, all nodes have `first_upload_time <= time`, and at least one common Python version across the resolved set (via `py_mask` intersection).

## How it works

1. **Data loading (once)**  
   All in-memory collections are loaded from MongoDB at startup:
   - `global_graph_node_ids`, `global_graph_name_ids`, `global_graph_requires_python_with_timestamps`
   - `global_graph_adj_deps` (per-node direct dependency name_ids)
   - `global_graph_adj_headers` (per (src_id, dep_name_id) chunk time bounds)

   Chunk data (`global_graph_adj_chunks`) is **not** fully loaded; it is fetched on demand and cached in an **LRU cache** (default 200k keys, configurable).

2. **Resolution (per call)**  
   For each `(node_id, root_node_id, root_name_id, time)`:
   - A **provider** is built that uses the loaded context and implements resolvelib’s `AbstractProvider` (identify, find_matches, get_dependencies, is_satisfied_by, get_preference).
   - **Root pinning**: when the resolver asks for candidates for `root_name_id`, the provider returns only `root_node_id`.
   - **Time**: only candidates with `first_upload_time <= time` are considered; chunk/header binary search yields candidates **newest-first**.
   - **Python**: an optional `set_state` hook in the copied resolvelib lets the provider see the current pinned mapping and intersect `py_mask` so only Python-compatible candidates are yielded.
   - The **resolver** (resolvelib’s `Resolver`) runs with this provider; no custom backtracking or resolution logic is implemented here.

3. **Result**  
   On success, **depth** is computed by BFS from the start node to the root in the result graph; the **dependency tree** (nodes and edges by node_id) is built if `debug=True`.

## Interfaces exposed

### Loading

- **`load_context(mongo_uri=..., pypi_db=..., chunk_cache_cap=200_000)`**  
  Connects to MongoDB, loads all in-memory collections, and returns a **`ResolutionContext`**. Requires **pymongo**. Chunks are loaded on demand and cached.

- **`ResolutionContext`**  
  Holds:
  - `node_py_mask`, `node_time`, `node_name_id` (lists indexed by node_id)
  - `name_id_to_name`, `adj_deps`, `adj_headers`
  - `chunk_lru` and `chunks_coll` for on-demand chunk access  
  You can also construct a context manually (e.g. for tests) instead of calling `load_context`.

### Resolution

- **`ResolutionRunner(ctx)`**  
  Wraps a `ResolutionContext`. Call **`resolve(...)`** on it for each resolution run.

- **`runner.resolve(node_id, root_node_id, root_name_id, time=None, debug=False, max_rounds=100)`**  
  Returns **`(resolved, depth, dependency_tree)`**:
  - **`resolved`**: `True` if a consistent assignment exists (root pinned, all deps satisfied, all nodes ≤ time, common Python version).
  - **`depth`**: minimum hops from `node_id` to `root_node_id` in the dependency tree, or `-1` if not resolved or root not in tree.
  - **`dependency_tree`**: if `debug=True` and resolved, a dict with `"nodes"`, `"edges"`, and `"mapping"` (name_id → node_id); otherwise `None`.

- **`resolve_one(ctx, node_id, root_node_id, root_name_id, time=None, debug=False)`**  
  One-shot helper: builds a `ResolutionRunner` from `ctx` and returns the same triple.

### Types (for custom code)

- **`Requirement(name_id, parent)`**  
  `parent` is a `Candidate` or `None` (root requirement).

- **`Candidate(node_id, name_id)`**  
  A single (package, version) identified by node_id.

Identifier type **KT** in resolvelib is **`int`** (name_id).

## Directory layout

```
pipstyle/
  __init__.py       # Exports: ResolutionRunner, resolve_one, load_context, ResolutionContext
  loader.py         # load_context(), ResolutionContext, LRU, DepHeader
  chunks.py         # iter_candidates_newest_first(), edge_exists_upto_t()
  structures.py     # Candidate, Requirement
  provider.py       # DBProvider(AbstractProvider)
  entrypoint.py     # ResolutionRunner, resolve_one(), depth/tree helpers
  resolvelib/       # Copied from src/pip/_vendor/resolvelib/ (+ optional set_state hook)
  README.md         # This file
```

## Dependencies

- **pymongo** (only for `load_context()` when loading from MongoDB).
- Standard library only for the rest; resolvelib is self-contained under `pipstyle/resolvelib/`.

## Example

```python
from pipstyle import load_context, ResolutionRunner

ctx = load_context(
    mongo_uri="mongodb://localhost:27017",
    pypi_db="pypi_dump",
    chunk_cache_cap=200_000,
)
runner = ResolutionRunner(ctx)

resolved, depth, tree = runner.resolve(
    node_id=4553200,
    root_node_id=5410000,
    root_name_id=12345,
    time=1730000000,  # epoch
    debug=True,
)
print(resolved, depth)
if tree:
    print("Nodes:", tree["nodes"], "Edges:", tree["edges"])
```

## Batch script: run.py

From the repo root (the `pip` directory), run resolution for every node in a subgraph (one root bit):

```bash
python3 -m pipstyle.run --subgraph urllib3_subgraph --root-bit-index 82 --output-dir output [--debug]
```

Or for a smaller example:
```bash
python3 -m pipstyle.run --subgraph pyxdg_subgraph --root-bit-index 1 --output-dir output
```

- **--subgraph**: Subgraph collection name (e.g. `urllib3_subgraph`).
- **--root-bit-index**: Root version bit (0..nbits-1); default is latest.
- **--output-dir**: Directory for output CSV and, if `--debug`, a subdir of resolved trees.
- **--chunk-cache-cap**: LRU cap for chunks (default 200000).
- **--debug**: Also write each resolved dependency tree as `<output_dir>/<subgraph>_<rootBit>_resolved_trees/<node_id>.json`.

Output CSV: `<output_dir>/<subgraph>_<rootBit>.csv` with columns `node_id`, `resolved`, `depth`. The script prints final stats: total processed, resolved, resolved+reached (depth >= 0), resolved+not reached (depth -1), not resolved.

## References

- **Plan**: `markdowns/resolution_plan_db_backed.md`
- **Task**: `markdowns/task_definition.md`
- **Collections**: `markdowns/db_collections_reference.md`
- **Phase4 (reference)**: `external/phase4_exposure_nodes_1.py`
