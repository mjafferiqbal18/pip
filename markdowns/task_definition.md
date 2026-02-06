### Relevant directories of pip:
- src/pip/_vendor/resolvelib/ (for resolution + backtracking)
- src/pip/_internal/resolution/resolvelib/ (for data provision)

### Relevant context about the existing collections:
- markdowns/db_collections_reference.md

### Relevant context about existing pip execution trace:
- markdowns/pandas_execution_trace.md

### Task Definition:
- Instead of querying PyPI endpoints for finding the latest version of a root package, finding its constraints, its direct dependencies (packages) and qualifying versions (package,versions) that satisfy constraints(and repeating the same tasks when recursing on the dependencies to resolve them in hopes of finding a satisfying assignment for all packages and a Python version that can support them), we want to use our preprocessed collections in our mongodb (described in detail in `markdowns/db_collections_reference.md`), all of which (apart from *_chunks) can be loaded into memory at the start.

### Inputs to be provided (high level):
- `node_id` (the package,version for which we want to perform dependency resolution)
- `root_node_id` and `root_name_id`: (the root package,version[`root_node_id`] which we want to pin. We require a satisfying assignment for node_id given that `root_node_id` is the ONLY candidate available for `root_name_id`. For example, node_id could require a package P as a dependency with multiple qualifying versions that satisfy global constraints so far: [p1,p2,p3,..]. However, when node_id requires (either directly or transitively during recursive resolution) a package with `name_id` == `root_name_id`, then `root)node_id` should be the only available candidate). The goal is to find a satisfying assignment for `node_id` such that BOTH `node_id` and `root_node_id` are pinned (only these specific versions of these packages can be used).
- `time`: All candidate versions are filtered by this `time`. So all candidate versions' first_upload_time must be <= time (i.e. they must exist by time). 

### Context about the Input and what it represents:
- for a subgraph (from the subgraphs db) rooted at a node `root_node_id`, we can retrieve all nodes present in its subgraph. A `node_id` (in the subgraph of `root_node_id`) is connected to `root_node_id` either directly or transitively. As mentioned in [markdowns/db_collections_reference.md], the subgraph is built using a reverse adjacency collection (which in turn was built from the global_graph, which in turn was built via parsing requires_dist strings of packages from distribution_metadata collection). Thus, we perform dependency resolution for `node_id` keeping `root_node_id` pinned as well to see if a `node_id` can really depend (or atleast co-exist) with `root_node_id` at a given time.
- `time` = max (first_upload_time[`node_id`], first_upload_time[`root_node_id`]) which guarantees both exist at that point in time.

### Outputs to be expected (high level):
- `resolved`: True/False denoting if we found a satisfying assignment for `node_id`'s dependencies (direct and transitive) such that `root_node_id` was also pinned, all `nodes` (i.e. package,version) in the dependency tree exist on t <= `time`, and all `nodes` have agree on >=1 Python Versions.
- `depth`: integer value >=0, -1 is Default: if `resolved`==True, it means there existed a satisying assignment where `node_id` and `root_node_id` co-exist. It doesn't necessarily mean that `node_id` explicitly depends on `root_node_id` in the satisfying assignment. So one can perform a DFS on the resolved dependency tree starting at `node_id` to see if it reaches `root_id`, and the minimum hops from `node_id` to `root_node_id` if reached.
- `dependency Tree`: Final dependency tree structure if `resolved`==True and args.debug==1, Default= None.

### Required Workflow:
- Perform dependency resolution for `node_id`, keeping `root_node_id` pinned and only using candidates that exist before or at `time`, while also making sure that there exists atleast 1 satisfying Python Version
- 

### Potential Caches to improve performance:
- All collections that can be loaded into memory (`global_graph_node_ids`,`global_graph_name_ids`,`global_graph_requires_python_with_timestamps`,`global_graph_adj_deps`,`global_graph_adj_headers`) should be loaded into memory at the start. Use a LRU cache for `global_graph_adj_chunks` with a default cap size of 200k keys. Store all chunk data (not time truncated data) in the LRU cache.
