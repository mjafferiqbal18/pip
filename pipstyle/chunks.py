"""
Chunk-based candidate enumeration: get candidates for (src_id, dep_name_id) up to time t,
newest-first. Uses binary search on headers and within chunks.
"""

from __future__ import annotations

from typing import Iterator, List, Optional

from pipstyle.loader import DepHeader, ResolutionContext


def _bisect_right_by_time(
    ctx: ResolutionContext,
    dst_ids: List[int],
    t: int,
) -> int:
    """
    dst_ids sorted by first_upload_time ascending.
    Return index i such that dst_ids[:i] have time <= t.
    """
    node_time = ctx.node_time
    lo, hi = 0, len(dst_ids)
    while lo < hi:
        mid = (lo + hi) // 2
        nid = dst_ids[mid]
        tm = node_time[nid] if nid < len(node_time) else None
        if tm is None or tm > t:
            hi = mid
        else:
            lo = mid + 1
    return lo


def iter_candidates_newest_first(
    ctx: ResolutionContext,
    src_id: int,
    dep_name_id: int,
    t: int,
    root_name_id: Optional[int] = None,
    root_node_id: Optional[int] = None,
) -> Iterator[int]:
    """
    Yield dst node_ids for (src_id, dep_name_id) with first_upload_time <= t,
    newest-first. If root_name_id and root_node_id are set and dep_name_id == root_name_id,
    yield only root_node_id (if valid at t).
    """
    if root_name_id is not None and root_node_id is not None and dep_name_id == root_name_id:
        if root_node_id < len(ctx.node_time):
            tm = ctx.node_time[root_node_id]
            if tm is not None and tm <= t:
                yield root_node_id
        return

    h = ctx.get_header(src_id, dep_name_id)
    if h is None or not h.chunks:
        return
    if h.min_t is not None and h.min_t > t:
        return

    for ci in reversed(h.chunks):
        if ci.min_t is not None and ci.min_t > t:
            continue
        dst_ids = ctx.get_chunk(src_id, dep_name_id, ci.chunk)
        if not dst_ids:
            continue
        if ci.max_t is not None and ci.max_t <= t:
            cut = len(dst_ids)
        else:
            cut = _bisect_right_by_time(ctx, dst_ids, t)
        for i in range(cut - 1, -1, -1):
            nid = dst_ids[i]
            tm = ctx.node_time[nid] if nid < len(ctx.node_time) else None
            if tm is None or tm > t:
                continue
            yield nid


def edge_exists_upto_t(
    ctx: ResolutionContext,
    src_id: int,
    dep_name_id: int,
    dst_id: int,
    t: int,
) -> bool:
    """True iff dst_id is among candidates for (src_id, dep_name_id) with time <= t."""
    h = ctx.get_header(src_id, dep_name_id)
    if h is None or not h.chunks:
        return False
    if h.min_t is not None and h.min_t > t:
        return False
    for ci in h.chunks:
        if ci.min_t is not None and ci.min_t > t:
            break
        dst_ids = ctx.get_chunk(src_id, dep_name_id, ci.chunk)
        if ci.max_t is not None and ci.max_t <= t:
            cut = len(dst_ids)
        else:
            cut = _bisect_right_by_time(ctx, dst_ids, t)
        for i in range(cut):
            if dst_ids[i] == dst_id:
                return True
    return False
