# Detailed Plan: DB-Backed Dependency Resolution (pip-style)

This document provides a step-by-step plan to implement dependency resolution that uses preprocessed MongoDB collections instead of querying PyPI, with a pinned root package and time-based candidate filtering.

**Core principle**: Do **not** reimplement resolution or backtracking. Copy pip’s resolvelib **in full** and integrate solely via a **DB-backed provider** and minimal Requirement/Candidate types. All data handling (DB, time, root pinning, Python masks) lives in the provider; the existing resolution and backtracking logic is reused unchanged.

---

## 1. Goal Summary

- **Replace**: PyPI API usage with reads from existing MongoDB collections.
- **Scope**: Resolve dependencies for a given `node_id` while keeping `root_node_id` pinned for `root_name_id`, with all candidates having `first_upload_time <= time`, and at least one common Python version across the resolved set.
- **Output**: `resolved` (bool), `depth` (int, default -1), and optionally the dependency tree (if `args.debug==1`).
- **Code location**: New `pipstyle/` directory.
- **Strategy**:
  - **Copy** the complete `src/pip/_vendor/resolvelib/` tree into `pipstyle/resolvelib/` (resolution + backtracking). Do **not** rewrite or reimplement this logic.
  - **Integrate** by implementing resolvelib’s `AbstractProvider` and minimal `Requirement`/`Candidate` types that work with the copied resolver. Root pinning, time filtering, and Python mask handling are done **entirely in the provider** (and in chunk/candidate enumeration). Any change to the copied resolvelib should be **minimal** (e.g. an optional hook for Python state, if needed; see §6).

---

## 2. Inputs and Outputs (Reference)

| Input | Type | Meaning |
|-------|------|--------|
| `node_id` | int | The (package, version) for which we perform resolution. |
| `root_node_id` | int | The root (package, version) we pin. |
| `root_name_id` | int | Name id of the root package; when any dependency has `name_id == root_name_id`, the only allowed candidate is `root_node_id`. |
| `time` | int (epoch) | Cutoff: every candidate must have `first_upload_time <= time`. |

| Output | Type | Meaning |
|--------|------|--------|
| `resolved` | bool | True iff a satisfying assignment exists (all deps satisfied, root pinned, all nodes ≤ time, ≥1 common Python version). |
| `depth` | int | If resolved: min hops from `node_id` to `root_node_id` in the dependency tree (DFS). Default -1. |
| `dependency_tree` | object or None | If `resolved` and `args.debug==1`, the final dependency tree; else None. |

**Context**: `time = max(first_upload_time[node_id], first_upload_time[root_node_id])` so both nodes exist at that time.

---

## 3. Collections and Data Loading

### 3.1 Load into memory at startup

All of the following (except `*_chunks`) are loaded fully into memory when initializing the data provider / app:

| Collection | In-memory structure | Purpose |
|------------|----------------------|--------|
| `global_graph_node_ids` | Map: `node_id → (name, version)` or reverse lookup as needed | Resolve node_id to package identity. |
| `global_graph_name_ids` | Map: `name_id → name` (and optionally `name → name_id`) | Resolve name_id to package name. |
| `global_graph_requires_python_with_timestamps` | Arrays/lists indexed by `node_id`: `py_mask`, `first_upload_time` (epoch) | Time filter and Python compatibility. |
| `global_graph_adj_deps` | Map: `src_id → list[dep_name_id]` | Direct dependencies (by name_id) for each node. |
| `global_graph_adj_headers` | Map: `(src_id, dep_name_id) → { mi[], ma[], n[], total }` | Per-chunk time bounds for binary search. |

Implementation detail: use lists indexed by `node_id` where possible (as in `phase4_exposure_nodes_1.py`: `node_py_mask`, `node_time`, `node_name_id`) for O(1) access; ensure max node_id is known and arrays are sized to `max_node_id + 1`.

### 3.2 LRU cache for chunks

- **Collection**: `global_graph_adj_chunks`
- **Key**: `(src_id, dep_name_id, chunk)` (or equivalent).
- **Value**: Full chunk data (all `dst_ids` in that chunk), **not** truncated by time. Time filtering is done when iterating candidates, not when caching.
- **Cap**: Default 200k keys; configurable via arguments (e.g. `--chunk-cache-cap`).
- **Usage**: On candidate lookup for `(src_id, dep_name_id)` up to time `t`, use headers to find chunk index via binary search on `ma`, then binary search within the (cached or fetched) chunk to get the last candidate with `first_upload_time <= t`. Yield candidates newest-first (reverse order within chunk and across chunks).

---

## 4. Candidate Filtering by Time (Workflow)

- For a given `(src_id, dep_name_id)` and cutoff `t`:
  1. Get header from in-memory `global_graph_adj_headers` (or equivalent).
  2. Use **binary search on `ma`** (max time per chunk) to find the last chunk index `c` such that `ma[c]` is still relevant (e.g. chunk has some candidate with time ≤ t). Then consider chunks 0..c (or the appropriate range).
  3. For the last chunk that might contain time-filtered candidates, load the chunk (via LRU cache or DB), then **binary search within `dst_ids`** using `first_upload_time` to find the last index `i` with `time(dst_ids[i]) <= t`. Candidates in that chunk are `dst_ids[:i+1]`; present them **newest-first** (e.g. reverse iterate).
  4. For earlier chunks, if `ma[chunk] <= t`, the entire chunk is eligible; yield its `dst_ids` in reverse order (newest first).
- Ensure no duplicate node_ids and that ordering is “newest first” globally when combining multiple chunks (e.g. iterate chunks in reverse, then within chunk in reverse).

---

## 5. Identifier and Type Mapping (Minimal Data Structures)

- **Identifier (KT)**: Use **name_id** (int). One “requirement” per package name; the resolver pins one candidate per name_id.
- **Candidate (CT)**: Use a minimal object that carries **node_id** (and optionally name_id, py_mask, first_upload_time if needed for provider logic). Do **not** add fields that are never populated (e.g. “extra requirements”) to keep overhead low.
- **Requirement (RT)**: Minimal. Enough to identify the dependency (e.g. name_id + parent node_id for context). No specifier/constraint parsing; “satisfaction” is “candidate’s node_id is in the allowed set for this (parent, name_id) at time t”.

**Key adaptations**:

- **No constraint intersection**: We do not merge version specifiers. Instead we **intersect sets of allowed candidates** (node_ids) for a given name_id across parents, and/or **restrict to root_node_id** when name_id == root_name_id.
- **Python compatibility**: **Intersect py_masks** over chosen nodes; if the resulting mask is 0, the set is incompatible. No `Requires-Python` specifier object; use masks from `global_graph_requires_python_with_timestamps`.
- **Root pinning**: Whenever the resolver asks for candidates for `root_name_id`, return only `root_node_id` (it is guarunteed to exist at time `t`).

---

## 6. Using pip's Resolution and Backtracking (No Reimplementation)

### 6.1 Copy resolvelib as-is

- **Copy** the entire directory `src/pip/_vendor/resolvelib/` into `pipstyle/resolvelib/` (including `__init__.py`, `providers.py`, `reporters.py`, `structs.py`, and the `resolvers/` package: `abstract.py`, `criterion.py`, `exceptions.py`, `resolution.py`). This preserves the full resolution and backtracking logic.
- **Do not** reimplement `Resolution`, `State`, `Criterion`, or the backtracking loop. The resolver calls the provider's `identify`, `find_matches`, `is_satisfied_by`, `get_dependencies`, and `get_preference`; we implement those so the existing code works with DB-backed data and root pinning.

### 6.2 How the resolver uses the provider

- **identify(requirement_or_candidate)** -> `KT` (our `name_id`). Used to group requirements and look up criteria.
- **find_matches(identifier, requirements, incompatibilities)** -> iterable of `CT`. The resolver passes:
  - `identifier`: the name_id being resolved.
  - `requirements`: mapping such that `requirements[identifier]` is an iterator of our `Requirement` objects (each has `.parent` candidate, or `None` for the root requirement).
  - `incompatibilities`: candidates to exclude.
  We derive **parent node_ids** from the requirements and compute the **allowed candidate set** (intersect over parents, root pinning, time, then filter by Python; see §7.5).
- **is_satisfied_by(requirement, candidate)** -> bool. True when the candidate is valid for this requirement (same name_id, edge exists from requirement.parent to candidate at time t, root respected).
- **get_dependencies(candidate)** -> iterable of `RT`. One Requirement per dep_name_id from `adj_deps[candidate.node_id]`.
- **get_preference(...)** -> sort key; can be a constant or prefer fewer candidates.

Root pinning, time cutoff, and intersecting allowed candidates / py_masks are all done **inside the provider**. The resolver code stays unchanged.

### 6.3 Optional minimal change in copied resolvelib (Python mask)

- `find_matches` does not receive the current state (pinned mapping). To filter by **current Python mask**, the provider needs the current mapping when `find_matches` runs.
- **Minimal hook**: In the **copied** `pipstyle/resolvelib/resolvers/resolution.py`, before calling `self._p.find_matches(...)`, add: `getattr(self._p, 'set_state', lambda s: None)(self.state)`. Our provider implements `set_state(state)` and stores `state.mapping`; in `find_matches` we compute `allowed_py` from pinned candidates and only yield candidates with `py_mask & allowed_py != 0`. Other providers ignore `set_state`. No change to `AbstractProvider` interface.

### 6.4 Entrypoint: drive Resolution and compute depth/tree

- **Entrypoint** will: (1) Build the provider (loader, chunks, root_node_id, root_name_id, time, start node_id). (2) Create a root requirement for the name_id of `node_id` with `parent=None` so the only candidate for that name_id is `node_id`. (3) Instantiate resolvelib's `Resolution(provider, reporter)` and add that root requirement. (4) Call `resolution.resolve()`. (5) On success: from `Result` (mapping, graph) set `resolved=True`, compute `depth` (BFS/DFS from node_id to root_node_id), and optionally build the dependency tree for debug. On failure: `resolved=False`, `depth=-1`, tree=None.
- No custom resolver module: use `pipstyle/resolvelib/resolvers/resolution.py` for all resolution and backtracking.

## 7. Component Breakdown (pipstyle/)

### 7.1 Directory layout (suggested)

```
pipstyle/
  __init__.py
  config.py          # or args: mongo uri, db names, cache caps, debug
  loader.py          # load in-memory collections + init LRU for chunks
  structures.py      # minimal Requirement, Candidate, State-like structs (node_id / name_id based)
  provider.py        # AbstractProvider impl: identify, find_matches, get_dependencies, is_satisfied_by
  chunks.py          # chunk access + binary search + “candidates for (src_id, dep_name_id, t)” newest-first
  entrypoint.py           # init (load + cache), resolve(...) -> (resolved, depth, dependency_tree); drives resolvelib Resolution
  resolvelib/             # COPIED from src/pip/_vendor/resolvelib/ (unchanged except optional set_state hook in resolution.py)
```
There is **no** custom `resolver.py`: resolution and backtracking live entirely in `pipstyle/resolvelib/`.

### 7.2 loader.py

- Connect to MongoDB (uri from config/args).
- Load:
  - `global_graph_node_ids` → decide structure (e.g. list by node_id, or dict node_id → {name, version}; and build node_id → name_id using name_ids collection).
  - `global_graph_name_ids` → name_id → name; optionally name → name_id.
  - `global_graph_requires_python_with_timestamps` → `node_py_mask`, `node_time` (epoch) as lists indexed by node_id; handle null/missing (e.g. mask = all-ones, time = None).
  - `global_graph_adj_deps` → dict or list: src_id → list of dep_name_id.
  - `global_graph_adj_headers` → (src_id, dep_name_id) → { mi, ma, n, total } (lists per chunk).
- Instantiate LRU cache for chunks (key (src_id, dep_name_id, chunk), value = list of dst_ids; full chunk, no time truncation).
- Expose a single “data provider” or “context” object that holds these and the chunk cache, and is passed to the provider and resolver.

### 7.3 chunks.py (candidate enumeration)

- **get_candidates(src_id, dep_name_id, t, node_time, root_name_id, root_node_id)**:
  - If dep_name_id == root_name_id: return [root_node_id] if root_node_id’s time <= t else [].
  - Else: use headers + LRU chunks to:
    - Determine relevant chunk(s) by binary search on `ma`.
    - For each chunk, get dst_ids (from cache or DB); if chunk’s max <= t use all, else binary search by time in chunk.
    - Yield node_ids **newest-first** (reverse order), no duplicates.
- Use **node_time** array (indexed by node_id) for time comparison; get times from the preloaded requires_python_with_timestamps data.

### 7.4 structures.py

- **Candidate**: e.g. a small dataclass with `node_id`, `name_id` (derived), and optionally `py_mask` and `first_upload_time` if the resolver needs them (or read from global arrays by node_id).
- **Requirement**: e.g. (name_id, parent_node_id or parent candidate) so the provider can compute “allowed set” for this name_id from parent’s edges and time.
- Do **not** define custom State or Result: resolvelib supplies these (mapping, criteria, graph). We only supply RT (Requirement) and CT (Candidate) that satisfy the provider contract.
- Keep everything minimal: no specifiers, no extras, no constraint objects.

### 7.5 provider.py

- **identify(requirement_or_candidate)**:
  - If requirement: return requirement.name_id.
  - If candidate: return candidate.name_id (or node_id → name_id from global lookup).
- **find_matches(identifier, requirements, incompatibilities)** (resolvelib signature):
  - `identifier` is name_id. Iterate `requirements[identifier]` to get all Requirement objects; from each, get `.parent` (Candidate or None). If any parent is None, this is the root requirement -> allowed set = { start_node_id }. If identifier == root_name_id -> allowed set = { root_node_id } (if valid at time t). Else: for each parent node_id, get candidates from chunks for (parent.node_id, identifier, t); **intersect** these sets; filter by time and (if set_state is used) by current Python mask. Exclude candidates in `incompatibilities[identifier]`. Return iterable of Candidate in **newest-first** order.

- **get_dependencies(candidate)**:
  - candidate has node_id. Read direct deps from adj_deps: list of dep_name_id. For each dep_name_id, create a Requirement(name_id=dep_name_id, parent=candidate). No Requires-Python requirement object; Python is handled by intersecting py_masks when evaluating candidates.
- **is_satisfied_by(requirement, candidate)**:
  - True iff candidate’s name_id == requirement.name_id and candidate’s node_id is in the allowed set for (requirement.parent_node_id, requirement.name_id) at time t (and passes root pinning if name_id == root_name_id).
- **get_preference**: Prefer smaller candidate set or newer versions; can use “newest first” iteration so first match is preferred, or implement a key that prefers e.g. higher first_upload_time.

The provider must hold reference to: loaded deps/headers, chunk cache, node_time, node_py_mask, root_node_id, root_name_id, and current time `t` for the resolution run.

### 7.6 No custom resolver

- **Use the copied resolvelib only.** Do not add a custom `resolver.py`. The `Resolution` class in `pipstyle/resolvelib/resolvers/resolution.py` performs all resolution and backtracking. Our entrypoint instantiates it with our provider and calls `resolution.resolve()`; depth and dependency tree are computed from the returned `Result` (mapping, graph) after a successful run. Do **not** reimplement the backtracking loop.

### 7.7 entrypoint.py

- **init(connection_params, cache_cap=200_000, ...)**:
  - Call loader to load all in-memory collections and create chunk LRU.
  - Return an object that holds the loaded context and exposes:
- **resolve(node_id, root_node_id, root_name_id, time, debug=False)**:
  - Build provider (with time, root pinning); instantiate resolvelib's `Resolution(provider, reporter)`; add root requirement for node_id; call `resolution.resolve()`; from Result compute resolved, depth, and optional dependency tree.
  - Return:
    - `resolved: bool`
    - `depth: int` (-1 if not resolved or root not in tree)
    - `dependency_tree`: optional structure if resolved and debug=True.

---

## 8. Edge Cases and Invariants

- **Root not required in tree**: The task says we resolve node_id “keeping root_node_id pinned”. So if during resolution we never need root_name_id as a dependency, we still require that the root is “pinned” (only candidate for root_name_id). The depth is then computed only if root_node_id appears in the resolved tree; if it does not, depth can be -1 (or as defined: “minimum hops from node_id to root_node_id if reached”).
- **Missing data**: If node_id or root_node_id has no first_upload_time or py_mask, define policy (e.g. skip resolution or treat as invalid)-> no, it is guaranteed that this data is present.
- **Empty candidate set**: If for some name_id (other than root) there are no candidates ≤ time, resolution fails (resolved=False).
- **Chunk cache**: Store full chunk; time filtering is applied at iteration time so the same cache entry is valid for any t.

---

## 9. Testing and Validation

- Reuse or mirror tests from resolvelib where applicable (e.g. state transitions, backtracking).
- Validate against known (node_id, root_node_id, root_name_id, time) tuples: e.g. from subgraph metadata and exposure CSV (phase4), compare “resolved” and “depth” with existing exposure results.
- Unit tests: loader (in-memory structures and cache), chunk binary search and newest-first iteration, provider identify/find_matches/get_dependencies for a small fixture.

---

## 10. Implementation Order (Suggested)

1. **Copy resolvelib**: Copy the complete `src/pip/_vendor/resolvelib/` tree into `pipstyle/resolvelib/`. Optionally add the minimal `set_state` hook in `resolution.py` (see §6.3) if Python-mask filtering is needed in `find_matches`.
2. **loader.py**: Load all in-memory collections; implement LRU and load one chunk on demand to verify connectivity and schema.
3. **chunks.py**: Implement header-based chunk selection and binary search within chunk; implement `get_candidates(..., t)` with root pinning and newest-first order.
4. **structures.py**: Define Candidate and Requirement (minimal; resolvelib supplies State, Criterion, Result).
5. **provider.py**: Implement AbstractProvider (identify, find_matches with requirements/incompatibilities, get_dependencies, is_satisfied_by, get_preference; optional set_state); wire in loader, chunks, node_time, node_py_mask, root pinning.
6. **entrypoint.py**: Init (load + cache); build provider and resolvelib `Resolution`; add root requirement and call `resolution.resolve()`; from Result compute resolved, depth, and optional dependency tree; parse args (node_id, root_node_id, root_name_id, time, debug, chunk_cache_cap).
7. **CLI / script**: Optional script that calls entrypoint with args (and optionally reads from same DB as phase4) for ad-hoc runs and regression tests.

## 11. References

- **Task**: `markdowns/task_definition.md`
- **Collections**: `markdowns/db_collections_reference.md`
- **Pip execution trace**: `markdowns/pandas_execution_trace.md`
- **Pip resolution data structures**: `markdowns/pip_dependency_resolution_data_structures.md`
- **Reading from DB / chunk iteration**: `external/phase4_exposure_nodes_1.py` (AdjStore, get_dep_name_ids, get_header, get_chunk_dst_ids, iter_candidates_newest_first, edge_exists_upto_t; ExposureSolverCSP for CSP backtracking pattern)
- **Resolvelib (copy into pipstyle)**: `src/pip/_vendor/resolvelib/` — copy in full to `pipstyle/resolvelib/`; do not reimplement (providers, structs, resolvers/resolution.py).
- **Pip resolution (reference only)**: `src/pip/_internal/resolution/resolvelib/` (provider, factory, candidates, requirements) for how pip implements the provider interface.
