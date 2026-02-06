# Resolution Throughput Optimization Plan

## Current Performance Baseline
- **Throughput**: ~25-26 nodes/second (from terminal output: 7691 nodes in ~5 minutes)
- **Bottleneck**: Per-node resolution is the main throughput constraint
- **Memory**: 32GB RAM available, currently using ~10GB for in-memory collections

## Optimization Strategies

### 0. Frontier-Based Node Processing (High Impact - Foundation)
**Concept**: Process nodes in reverse BFS order (by dependency distance from root) to maximize cache reuse.

**Implementation**:
- **Frontier 0**: Root node (already resolved, depth=0)
- **Frontier 1**: Direct dependents of root (nodes that directly depend on root)
- **Frontier 2**: Nodes that depend on frontier 1 nodes
- **Frontier N**: Nodes that depend on frontier N-1 nodes

**Algorithm**:
1. Stream subgraph edges to build reverse dependency graph
   - For each edge `(src_id, dst_id)` in subgraph, add `dst_id -> src_id` to reverse graph (dst depends on src)
2. Perform reverse BFS from root to assign nodes to frontiers:
   ```python
   frontier = {root_id: 0}
   queue = deque([root_id])
   while queue:
       node = queue.popleft()
       for dependent in reverse_graph.get(node, []):
           if dependent not in frontier:
               frontier[dependent] = frontier[node] + 1
               queue.append(dependent)
   ```
3. Group nodes by frontier: `frontiers = {0: [root], 1: [...], 2: [...], ...}`
4. Process nodes frontier-by-frontier (0, 1, 2, ...)
5. Within each frontier, process nodes in sorted order (for determinism)

**Benefits**:
- **Maximizes negative cache hits**: Nodes in frontier N will encounter nodes from frontier N-1, N-2, etc. during resolution, which are likely already in the failure cache
- **Natural dependency ordering**: Resolve dependencies before dependents
- **Better cache locality**: Similar nodes (same frontier) processed together

**Cache Key Simplification**:
- Since all nodes in a subgraph use the same `root_node_id` and `root_name_id`, we can simplify cache keys to `(node_id, time)` instead of `(node_id, root_node_id, root_name_id, time)`
- This reduces memory usage and improves cache hit rates

**Expected Impact**: 40-60% improvement in cache effectiveness, enabling better memoization strategies

**Implementation Details**:
- Modify `collect_subgraph_nodes_for_bit()` to also return frontier assignments
- Or add separate function `assign_frontiers(subgraph_edges, root_id)` that returns `Dict[int, int]` (node_id -> frontier)
- Update `run.py` to process nodes by frontier instead of sorted order
- Cache (failure/success) is shared across all resolutions, so frontier-based ordering naturally maximizes reuse

### 1. Memoization Strategies

#### 1.1 Negative Failure Memoization (High Impact)
**Concept**: Cache failed resolutions to avoid redundant work.

**Implementation**:
- **Cache Key**: `(node_id, time)` - simplified since all nodes in subgraph share same root
- **Cache Value**: `False` (resolution failed) or timestamp of failure
- **Reuse Logic**: 
  - If `resolve(node_id=x, time=t1)` → `False` (with root r)
  - And we encounter `node_id=x` as a candidate during resolution of `node_id=y` with `time=t2 <= t1`
  - Then we can immediately reject `x` because:
    - **Time constraint**: t2 <= t1 means constraints are more restrictive
    - **Monotonicity**: If x failed with more lenient constraints (t1), it will fail with stricter ones (t2)
    - **Root constraint**: Same root (r) applies to all nodes in subgraph
    - **Important**: This only works when t2 <= t1. If t2 > t1, we cannot reuse the result because new paths/versions may be available

**Cache Structure**:
```python
# In ResolutionRunner (shared across all resolutions in subgraph)
self._failure_cache: LRUCache[Tuple[int, int], bool] = LRUCache(cap=1_000_000)
# Key: (node_id, time)
# Value: True if cached (False means failed)
```

**Optimization Details**:
- Use LRU cache with configurable size (e.g., 1M entries)
- Check cache before creating provider/resolver in `resolve()`
- Check cache when evaluating candidates in `find_matches()` and `is_satisfied_by()`
- When checking cache, look for any entry with same `node_id` and `time <= current_time`
- Frontier-based processing naturally maximizes cache hits (frontier N nodes encounter frontier N-1 nodes)

**Expected Impact**: 40-60% reduction in failed resolution attempts (higher with frontier-based processing)

#### 1.2 Positive Success Memoization (Medium Impact)
**Concept**: Cache successful resolutions and reuse as "first attempt" with validation.

**Implementation**:
- **Cache Key**: `(node_id, time)` - simplified since all nodes share same root
- **Cache Value**: `(True, depth, dependency_mapping, final_pymask)` where:
  - `dependency_mapping`: `name_id -> node_id` for resolved dependencies
  - `final_pymask`: Final Python mask intersection after resolution (needed for validation)
- **Reuse Logic**:
  - If we've resolved `(x, t1)` successfully → cached result R1
  - And we encounter `x` during resolution of `(y, t2)` where `t2 >= t1`
  - **Incremental validation approach**:
    1. Use cached result R1 as the "first attempt"
    2. Validate compatibility:
       - Check if all dependencies in R1 are still valid at t2 (time check)
       - Check if R1's `final_pymask` is compatible with current resolution's Python constraints
       - Check if R1's dependencies don't conflict with already-pinned candidates in current resolution
    3. If validation passes: reuse R1 (fast path)
    4. If validation fails: fallback to full resolution of x (slow path)

**Why This Works**:
- **Time monotonicity**: If x resolved at t1, and t2 >= t1, more versions are available (not fewer), so the cached solution should still be valid
- **Python mask validation**: The `final_pymask` tells us what Python versions were compatible. If current resolution's constraints are more restrictive, we need to check compatibility
- **Conflict detection**: Need to ensure cached dependencies don't conflict with already-chosen candidates

**Cache Structure**:
```python
# In ResolutionRunner (shared across all resolutions in subgraph)
self._success_cache: LRUCache[Tuple[int, int], Tuple[bool, int, Dict[int, int], int]] = LRUCache(cap=500_000)
# Key: (node_id, time)
# Value: (True, depth, dependency_mapping, final_pymask)
```

**Storage Considerations**:
- Storing full dependency mappings can be expensive
- Use LRU cache to limit memory usage
- Consider compressing or using more efficient representations for large mappings

**Expected Impact**: 15-25% reduction for nodes that resolve successfully (higher for nodes encountered multiple times)

#### 1.3 Sub-resolution Memoization (Deprecated/Redundant)
**Concept**: Cache resolution results for intermediate nodes encountered during resolution.

**Analysis**:
- **Overhead**: Storing intermediate results adds memory overhead and complexity
- **Redundancy**: This is largely redundant with:
  - **1.1 Negative Failure Memoization**: Failed sub-resolutions are already cached
  - **1.2 Positive Success Memoization**: Successful sub-resolutions are already cached
  - **0. Frontier-Based Processing**: Natural ordering means dependencies are resolved before dependents, maximizing cache reuse
- **Conclusion**: With frontier-based processing + 1.1 + 1.2, explicit sub-resolution memoization provides minimal additional benefit and adds overhead

**Recommendation**: Skip this optimization. Focus on frontier-based processing + 1.1 + 1.2 instead.

#### 1.4 Candidate Rejection Memoization (Removed - Redundant)
**Concept**: Cache which candidates were rejected during resolution attempts.

**Analysis - Why This Is Problematic**:
- **Redundancy with 1.1**: If a candidate `node_id=x` fails during resolution, it's already captured by negative failure memoization (1.1)
- **Correctness Concerns**: A candidate rejected in one context may be valid in another:
  - Different parent constraints (different `src_id` in edge check)
  - Different Python mask intersection (different set of pinned candidates)
  - Different time constraints (though 1.1 handles this)
  - Different resolution state (different already-pinned candidates)
- **Safe Scenarios**: The only safe scenario is when:
  - Same `node_id` and `time <= cached_time` (same as 1.1)
  - Same root (already handled by 1.1)
  - But this is exactly what 1.1 already does!

**Conclusion**: This optimization is redundant with 1.1 Negative Failure Memoization. When evaluating candidates, we should check the failure cache (1.1) rather than maintaining a separate candidate rejection cache.

**Implementation Note**: When implementing 1.1, ensure it's checked during candidate evaluation in `find_matches()` and `is_satisfied_by()` to achieve the same benefit.

### 2. Algorithm Optimizations

#### 2.1 Early Termination for Impossible Cases (High Impact)
**Concept**: Detect impossible cases before starting full resolution.

**Implementation**:
- **Check 1**: If `node_time[node_id] > time`, immediately return `False`
- **Check 2**: If `node_py_mask[node_id] & root_py_mask == 0`, immediately return `False`
- **Check 3**: If `node_id` has no path to `root_node_id` in the dependency graph (precomputed reachability), return `False`
- **Check 4**: If `node_id` depends on `root_name_id` but no valid edge exists to `root_node_id` at `time`, return `False`

**Expected Impact**: 5-15% reduction in unnecessary resolution attempts

#### 2.2 Dependency Ordering Optimization (Medium Impact)
**Concept**: Process dependencies in an order that maximizes cache hits and early failures.

**Implementation**:
- Order dependencies by:
  1. Most constrained first (fewer candidates)
  2. Most likely to fail first (based on historical data)
  3. Most frequently encountered first (maximize cache reuse)

**Expected Impact**: 10-20% improvement in resolution speed

#### 2.3 Candidate Pruning in `find_matches()` (Medium Impact)
**Concept**: More aggressive filtering before yielding candidates.

**Implementation**:
- Pre-filter candidates by Python mask before intersection
- **Skip candidates that are known to fail**: Check negative failure cache (1.1) before yielding
  - For candidate `node_id=x` at `time=t`, check if `(x, t_failed)` exists in cache where `t_failed >= t`
  - If found, skip this candidate (it's guaranteed to fail)
- Use bloom filter or set membership for fast rejection
- Early exit if intersection becomes empty

**Expected Impact**: 20-30% reduction in candidate evaluation (combines with 1.1)

#### 2.4 Incremental Python Mask Intersection (Low-Medium Impact)
**Concept**: Compute Python mask intersection incrementally as candidates are evaluated.

**Current**: Computes full intersection in `_allowed_py_mask()`
**Optimization**: Track intersection incrementally, stop early if mask becomes 0

**Expected Impact**: 5-10% improvement in Python filtering

### 3. Data Access Optimizations

#### 3.1 Batch Header Queries (High Impact)
**Concept**: Instead of querying headers one-by-one, batch multiple queries.

**Implementation**:
- Collect all `(src_id, dep_name_id)` pairs needed during a resolution
- Batch query MongoDB: `find({"$or": [{"src_id": s, "dep_name_id": d}, ...]})`
- Populate cache with batch results

**Expected Impact**: 30-50% reduction in MongoDB round trips for headers

#### 3.2 Batch Chunk Queries (High Impact)
**Concept**: Similar to headers, batch chunk queries.

**Implementation**:
- Collect all `(src_id, dep_name_id, chunk)` tuples needed
- Batch query: `find({"$or": [{"src_id": s, "dep_name_id": d, "chunk": c}, ...]})`
- Populate LRU cache with batch results

**Expected Impact**: 40-60% reduction in MongoDB round trips for chunks

#### 3.3 Prefetching Strategy (Medium Impact)
**Concept**: Predict which data will be needed and fetch ahead.

**Implementation**:
- When resolving `node_id=x`, prefetch:
  - Headers for all `(x, dep_name_id)` pairs
  - Chunks for the first few chunks of each header
- Use background thread or async fetching

**Expected Impact**: 10-20% improvement for cache misses

#### 3.4 MongoDB Index Optimization (Medium Impact)
**Concept**: Ensure optimal indexes exist for query patterns.

**Required Indexes**:
- `global_graph_adj_headers`: `{src_id: 1, dep_name_id: 1}` (compound index)
- `global_graph_adj_chunks`: `{src_id: 1, dep_name_id: 1, chunk: 1}` (compound index)
- `global_graph_adj_deps`: `{_id: 1}` (already indexed)

**Expected Impact**: 20-30% improvement in query performance

#### 3.5 Connection Pooling and Session Management (Low-Medium Impact)
**Concept**: Optimize MongoDB connection handling.

**Implementation**:
- Use connection pooling with appropriate pool size
- Reuse sessions for batch operations
- Consider read preferences for better load distribution

**Expected Impact**: 5-15% improvement in query latency

### 4. Code-Level Optimizations

#### 4.1 Reduce Object Creation Overhead (Medium Impact)
**Concept**: Minimize allocations during hot paths.

**Optimizations**:
- Reuse `DBProvider` instances when possible (same root, different start_node_id)
- Reuse `Requirement` objects
- Use `__slots__` for data classes to reduce memory overhead
- Pre-allocate lists/sets where size is known

**Expected Impact**: 10-20% improvement in resolution speed

#### 4.2 Optimize Hot Paths (High Impact)
**Concept**: Profile and optimize the most frequently called functions.

**Hot Paths to Optimize**:
1. `get_header()` - called for every dependency evaluation
2. `get_chunk()` - called for every candidate enumeration
3. `edge_exists_upto_t()` - called for every candidate validation
4. `iter_candidates_newest_first()` - called for every dependency
5. `is_satisfied_by()` - called many times per resolution

**Optimization Techniques**:
- Inline small functions
- Use local variables instead of attribute access
- Cache frequently accessed values
- Use `functools.lru_cache` for pure functions

**Expected Impact**: 15-30% improvement in resolution speed

#### 4.3 Use More Efficient Data Structures (Low-Medium Impact)
**Concept**: Replace inefficient data structures with faster alternatives.

**Examples**:
- Use `frozenset` instead of `set` for immutable sets (can be cached)
- Use `array.array` instead of `list` for numeric arrays
- Use `collections.deque` for FIFO queues (already used)
- Consider `numpy` arrays for large numeric operations (if memory allows)

**Expected Impact**: 5-15% improvement

### 5. Parallel Processing

#### 5.1 Parallel Node Resolution (High Impact)
**Concept**: Process multiple nodes in parallel.

**Implementation**:
- Use `multiprocessing.Pool` or `concurrent.futures.ThreadPoolExecutor`
- Process nodes in batches (e.g., 100 nodes per batch)
- Each worker has its own `ResolutionRunner` instance
- Share read-only `ResolutionContext` (thread-safe for reads)

**Challenges**:
- MongoDB connection sharing (use connection pooling)
- Cache coordination (each worker has its own cache)
- Result aggregation

**Expected Impact**: 4-8x improvement with 4-8 cores

#### 5.2 Parallel Candidate Evaluation (Medium Impact)
**Concept**: Evaluate multiple candidates in parallel during resolution.

**Implementation**:
- When `find_matches()` yields multiple candidates, evaluate them in parallel
- Use `concurrent.futures` for parallel `is_satisfied_by()` checks
- Aggregate results

**Expected Impact**: 2-3x improvement for resolutions with many candidates

#### 5.3 Async I/O for MongoDB Queries (Medium Impact)
**Concept**: Use async MongoDB driver for non-blocking queries.

**Implementation**:
- Use `motor` (async MongoDB driver) instead of `pymongo`
- Use `asyncio` for concurrent queries
- Batch queries asynchronously

**Expected Impact**: 30-50% improvement in I/O-bound operations

### 6. Caching Strategy Improvements

#### 6.1 Multi-Level Caching (Medium Impact)
**Concept**: Use multiple cache levels with different eviction policies.

**Implementation**:
- **L1 Cache**: Small, fast, in-memory (e.g., 10K entries, no eviction)
- **L2 Cache**: Medium, LRU (current implementation, 500K entries)
- **L3 Cache**: Large, disk-backed (for persistence across runs)

**Expected Impact**: 20-40% improvement in cache hit rate

#### 6.2 Cache Warming (Low-Medium Impact)
**Concept**: Pre-populate cache with likely-to-be-needed data.

**Implementation**:
- Before processing nodes, warm cache with:
  - Headers for root_node_id's dependencies
  - Chunks for most common (src_id, dep_name_id) pairs
  - Common failure patterns

**Expected Impact**: 10-20% improvement in initial throughput

#### 6.3 Cache Persistence (Low Impact)
**Concept**: Save cache to disk and reload on startup.

**Implementation**:
- Serialize cache to disk (pickle or msgpack)
- Reload on startup
- Useful for repeated runs on same subgraph

**Expected Impact**: 5-10% improvement for repeated runs

### 7. Resolution Algorithm Improvements

#### 7.1 Incremental Resolution (High Impact)
**Concept**: Reuse partial results from previous resolutions.

**Implementation**:
- When resolving `node_id=y` after `node_id=x`:
  - If they share dependencies, reuse resolved sub-graphs
  - Only resolve the differences

**Expected Impact**: 30-50% improvement for similar nodes

#### 7.2 Dependency Graph Precomputation (Medium Impact)
**Concept**: Precompute reachability and common paths.

**Implementation**:
- Precompute: "Can node_id=x reach root_node_id?"
- Precompute: "What are the common dependencies between nodes?"
- Store in memory for fast lookups

**Expected Impact**: 15-25% improvement in early termination

#### 7.3 Smart Backtracking (Low-Medium Impact)
**Concept**: Guide backtracking based on failure patterns.

**Implementation**:
- Track which candidates fail most often
- Try less likely-to-fail candidates first
- Learn from previous failures

**Expected Impact**: 10-20% reduction in backtracking depth

### 8. Memory Optimizations

#### 8.1 Lazy Loading of Unused Data (Medium Impact)
**Concept**: Only load data that's actually needed.

**Implementation**:
- Currently: Load all `adj_deps` into memory
- Optimization: Load on-demand with caching (similar to headers)
- Only load deps for nodes that are actually encountered

**Expected Impact**: Reduce memory usage, potentially allow larger caches

#### 8.2 Memory-Mapped Files (Low Impact)
**Concept**: Use memory-mapped files for large read-only data.

**Implementation**:
- Export large arrays to memory-mapped files
- Access via `mmap` for efficient memory usage

**Expected Impact**: Better memory utilization, allow larger datasets

## Implementation Priority

### Phase 1: Quick Wins (1-2 weeks)
1. **Frontier-Based Node Processing** - High impact, moderate effort (foundation for other optimizations)
2. **Negative Failure Memoization** - High impact, relatively easy (simplified cache key)
3. **Early Termination Checks** - High impact, easy
4. **Batch Header/Chunk Queries** - High impact, moderate effort
5. **Hot Path Optimizations** - Medium-high impact, moderate effort

**Expected Improvement**: 3-4x throughput (75-100 nodes/second)

### Phase 2: Medium Effort (2-4 weeks)
1. **Positive Success Memoization** - Medium-high impact (with incremental validation)
2. **Parallel Node Resolution** - High impact, requires testing (can process frontiers in parallel)
3. **Dependency Ordering** - Medium impact
4. **Candidate Pruning with Failure Cache** - Medium impact (integration of 1.1 into find_matches)

**Expected Improvement**: Additional 2-3x (150-300 nodes/second)

### Phase 3: Advanced (4-8 weeks)
1. **Incremental Resolution** - High impact, very complex
2. **Async I/O** - Medium impact, requires refactoring
3. **Multi-level Caching** - Medium impact
4. **Dependency Graph Precomputation** - Medium impact

**Expected Improvement**: Additional 1.5-2x (225-600 nodes/second)

## Measurement and Validation

### Metrics to Track
1. **Throughput**: Nodes processed per second
2. **Cache Hit Rates**: Header cache, chunk cache, failure cache
3. **MongoDB Query Count**: Queries per resolution
4. **Memory Usage**: Peak and average
5. **Resolution Time Distribution**: P50, P95, P99

### Benchmarking
- Create benchmark suite with representative subgraphs
- Measure before/after for each optimization
- Track regressions

## Key Insights and Design Decisions

### Frontier-Based Processing as Foundation
- **Critical insight**: Processing nodes in reverse BFS order (by dependency distance from root) maximizes cache reuse
- **Why it works**: Nodes in frontier N naturally encounter nodes from earlier frontiers during resolution, which are likely already cached
- **Enables**: Simplified cache keys (no need for root_node_id/root_name_id since all nodes share same root)

### Memoization Strategy Refinements

#### Negative Failure Memoization (1.1)
- **Simplified cache key**: `(node_id, time)` instead of `(node_id, root_node_id, root_name_id, time)`
- **Correctness requirement**: Can only reuse when `t2 <= t1` (more restrictive constraints)
- **Why safe**: If node failed with lenient constraints, it will fail with stricter ones (monotonicity)

#### Positive Success Memoization (1.2)
- **Incremental validation approach**: Use cached result as "first attempt", validate compatibility, fallback if needed
- **Key data**: Must store `final_pymask` for Python compatibility validation
- **Why safe**: If node resolved at t1 and t2 >= t1, more versions available (not fewer), so cached solution should still be valid

#### Sub-resolution Memoization (1.3) - Removed
- **Reason**: Redundant with 1.1 + 1.2 + frontier-based processing
- **Overhead**: Storing intermediates adds complexity without significant benefit

#### Candidate Rejection Memoization (1.4) - Removed
- **Reason**: Redundant with 1.1 (negative failure cache)
- **Correctness concern**: Candidate rejection is context-dependent (parent, Python mask, resolution state), making it unsafe to cache separately

### Cache Key Simplification Benefits
- **Memory savings**: 2 fewer integers per cache entry (33% reduction for 4-tuple → 2-tuple)
- **Better hit rates**: Fewer unique keys means more cache hits
- **Simpler implementation**: Less complexity in cache management

## Notes

- **Trade-offs**: Some optimizations may increase memory usage (caching) or code complexity
- **Testing**: Each optimization should be tested independently to measure impact
- **Profiling**: Use `cProfile` or `py-spy` to identify actual bottlenecks
- **Incremental**: Implement optimizations incrementally and measure impact
- **Correctness**: All memoization strategies must respect monotonicity (time constraints) and root constraints