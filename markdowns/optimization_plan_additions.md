## Additional Optimizations: depth=-1 Handling and Within-Frontier Processing

### Handling depth=-1 Results in Positive Success Memoization

**Important insight**: Nodes with `resolved=True, depth=-1` can also be cached and reused.

**What depth=-1 means**: Resolution succeeded (all dependencies satisfied), but no path exists from node to root in dependency tree.

**Why this matters**: The resolution result (dependency mapping, final_pymask) is still valid and reusable.

**Reuse logic**: Same as depth>=0 nodes:
- Cache: `(node_id, time)` → `(True, depth, dependency_mapping, final_pymask)` (store depth=-1 in cache)
- Reuse when `t2 >= t1` with validation
- The depth value (-1 vs >=0) doesn't affect reuse correctness, only the final output

**When correct to reuse**:
- **Time constraint**: `t2 >= t1` (same as depth>=0 case)
- **Python compatibility**: R1's `final_pymask` must be compatible with current resolution's constraints
- **Dependency validity**: All dependencies in R1's mapping must still be valid at t2
- **No conflicts**: R1's dependencies must not conflict with already-pinned candidates

**Key difference**: When reusing a depth=-1 result, the resulting depth may still be -1 (if no path to root), but the resolution itself is valid.

**Implementation**: No special handling needed - depth=-1 results are stored and retrieved the same way as depth>=0.

### Within-Frontier Processing Order

**Observation**: Within a frontier, multiple nodes may belong to the same package (same `name_id`).

**Processing strategies**:
1. **Group by name_id**: Group nodes by `name_id` within each frontier
2. **Order within group**: Process nodes in each group in a specific order:
   - **Option A: Newest first** (by `node_time` descending)
     - **Benefit**: Newer versions are more likely to be encountered as dependencies
     - **Negative cache hits**: If newest version fails, older versions likely fail too (can skip them)
     - **Positive cache hits**: If newest version succeeds, we can potentially reuse for older versions
   - **Option B: Oldest first** (by `node_time` ascending)
     - **Benefit**: Older versions have fewer dependencies (simpler resolutions)
     - **Negative cache hits**: If oldest version fails, we know the package is problematic early
     - **Positive cache hits**: Older versions' dependencies are more likely to be cached from earlier frontiers
3. **Recommendation**: **Newest first** within each name_id group
   - Maximizes negative cache hits (if newest fails, skip older)
   - Newer versions are more likely to be encountered as dependencies in later frontiers
   - Can use cached newer version results to validate older versions faster

### Partial Result Sharing Across Same name_id

**Concept**: When processing multiple nodes with same `name_id` in a frontier, share partial resolution results.

**Implementation**:
- **Shared dependency cache**: Cache dependency mappings at the `name_id` level
  - Key: `(name_id, time)` → Set of `node_id`s that successfully resolved with their dependency mappings
- **Reuse strategy**:
  - When resolving `node_id=x` (name_id=n, time=t1):
    - Check if any `node_id=y` (same name_id=n, time=t2 where t2 >= t1) has been resolved
    - If yes, use y's resolution as starting point (since newer versions have more dependencies)
    - Validate and adapt if needed
  - When resolving `node_id=x` (name_id=n, time=t1):
    - If `node_id=y` (same name_id=n, time=t2 where t2 < t1) failed, x may also fail (but not guaranteed)
    - Use as hint for early termination

**Benefits**:
- **Reduced redundant work**: Don't recompute similar dependency trees for different versions of same package
- **Faster resolution**: Use cached dependency structure as template
- **Better cache utilization**: Share results across versions of same package

**Challenges**:
- Need to validate that cached dependencies are still valid for different node_id
- Different versions may have different dependencies (even if same name_id)
- Need to handle version-specific constraints

**Expected Impact**: 10-20% additional improvement for frontiers with many nodes from same packages
