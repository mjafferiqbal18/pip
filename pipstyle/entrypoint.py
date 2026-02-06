"""
Entrypoint: initialize once (load DB context), then resolve(node_id, root_node_id, root_name_id, time).
Uses resolvelib's Resolver and Resolution; computes depth and optional dependency tree from Result.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from pipstyle.loader import ResolutionContext, load_context
from pipstyle.provider import DBProvider
from pipstyle.structures import Candidate, Requirement
from pipstyle.resolvelib.reporters import BaseReporter
from pipstyle.resolvelib.resolvers import (
    ResolutionImpossible,
    ResolutionTooDeep,
    Resolver,
)


def _compute_depth(
    result_mapping: Dict[int, Candidate],
    result_graph: Any,
    start_node_id: int,
    root_node_id: int,
) -> int:
    """
    BFS from start_node_id to root_node_id in the dependency graph.
    Graph vertices are name_ids (KT); we need to find path from start's name_id to root's name_id,
    then we can report hops. Actually depth is "min hops from node_id to root_node_id" in the
    *dependency* tree - i.e. edges are (parent, child) where parent depends on child. So we need
    to find path from start (as a vertex) to root (as a vertex). The graph in Result has vertices
    = name_id (identifier), and edges from parent to child (parent depends on child). So we need
    start_name_id and root_name_id. Then BFS from start_name_id to root_name_id in result_graph.
    Return number of edges (hops), or -1 if not reachable.
    """
    start_name_id = None
    root_name_id = None
    for name_id, cand in result_mapping.items():
        if cand.node_id == start_node_id:
            start_name_id = name_id
        if cand.node_id == root_node_id:
            root_name_id = name_id
    if start_name_id is None or root_name_id is None:
        return -1
    if start_name_id == root_name_id:
        return 0

    # result_graph: DirectedGraph with _forwards (parent -> set of children)
    # So we need to go from start_name_id to root_name_id following _forwards (dependency direction)
    # "depth" = min hops from node_id to root_node_id: that means we start at start and follow
    # "who depends on whom" - actually in the graph, edge A -> B means A depends on B. So from
    # start we follow outgoing edges (start's dependencies). To reach root we need a path
    # start -> ... -> root. So BFS from start_name_id using _forwards.
    seen = {start_name_id}
    q: deque[Tuple[int, int]] = deque([(start_name_id, 0)])
    forwards = result_graph._forwards
    while q:
        v, d = q.popleft()
        if v == root_name_id:
            return d
        for w in forwards.get(v, ()):
            if w not in seen:
                seen.add(w)
                q.append((w, d + 1))
    return -1


def _build_dependency_tree(
    result_mapping: Dict[int, Candidate],
    result_graph: Any,
) -> Dict[str, Any]:
    """Build a simple dependency tree structure: nodes and edges by node_id."""
    nodes = {c.node_id for c in result_mapping.values()}
    # Edges: (parent_node_id, child_node_id) for each dependency
    # result_graph has vertices = name_id, edges name_id -> name_id (parent depends on child)
    edges: List[Tuple[int, int]] = []
    name_id_to_node = {name_id: c.node_id for name_id, c in result_mapping.items()}
    for parent_name_id, children in result_graph._forwards.items():
        parent_node = name_id_to_node.get(parent_name_id)
        if parent_node is None:
            continue
        for child_name_id in children:
            child_node = name_id_to_node.get(child_name_id)
            if child_node is not None:
                edges.append((parent_node, child_node))
    return {"nodes": list(nodes), "edges": edges, "mapping": {k: v.node_id for k, v in result_mapping.items()}}


class ResolutionRunner:
    """
    Holds loaded ResolutionContext. Call resolve() with (node_id, root_node_id, root_name_id, time)
    to run resolution and get (resolved, depth, dependency_tree).
    """

    def __init__(self, ctx: ResolutionContext):
        self._ctx = ctx

    def resolve(
        self,
        node_id: int,
        root_node_id: int,
        root_name_id: int,
        time: Optional[int] = None,
        debug: bool = False,
        max_rounds: int = 100,
    ) -> Tuple[bool, int, Optional[Dict[str, Any]]]:
        """
        Run dependency resolution for node_id with root_node_id pinned.

        :param node_id: The (package, version) to resolve.
        :param root_node_id: The root (package, version) to pin.
        :param root_name_id: Name id of the root package; only root_node_id is allowed for that name.
        :param time: Cutoff time (epoch). If None, uses max(node_time[node_id], node_time[root_node_id]).
        :param debug: If True and resolved, return dependency_tree in the third element.
        :param max_rounds: Max resolution rounds (resolvelib).
        :return: (resolved, depth, dependency_tree or None).
        """
        ctx = self._ctx
        if time is None:
            tn = ctx.node_time[node_id] if node_id < len(ctx.node_time) else None
            tr = ctx.node_time[root_node_id] if root_node_id < len(ctx.node_time) else None
            if tn is None or tr is None:
                return False, -1, None
            time = max(tn, tr)

        start_name_id = ctx.node_name_id[node_id] if node_id < len(ctx.node_name_id) else None
        if start_name_id is None:
            return False, -1, None

        provider = DBProvider(
            ctx=ctx,
            start_node_id=node_id,
            root_node_id=root_node_id,
            root_name_id=root_name_id,
            t=time,
        )
        reporter = BaseReporter()
        resolver = Resolver(provider, reporter)
        root_requirement = Requirement(name_id=start_name_id, parent=None)

        try:
            result = resolver.resolve(requirements=[root_requirement], max_rounds=max_rounds)
        except (ResolutionImpossible, ResolutionTooDeep):
            return False, -1, None

        mapping = result.mapping
        graph = result.graph
        depth = _compute_depth(mapping, graph, node_id, root_node_id)
        tree = _build_dependency_tree(mapping, graph) if debug else None
        return True, depth, tree


def resolve_one(
    ctx: ResolutionContext,
    node_id: int,
    root_node_id: int,
    root_name_id: int,
    time: Optional[int] = None,
    debug: bool = False,
) -> Tuple[bool, int, Optional[Dict[str, Any]]]:
    """
    One-shot resolve using an existing context.
    """
    runner = ResolutionRunner(ctx)
    return runner.resolve(node_id, root_node_id, root_name_id, time=time, debug=debug)
