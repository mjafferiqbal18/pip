"""
DB-backed provider implementing resolvelib's AbstractProvider.
All candidate discovery, root pinning, time and Python filtering happen here.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Set

from pipstyle.chunks import edge_exists_upto_t, iter_candidates_newest_first
from pipstyle.loader import ResolutionContext
from pipstyle.structures import Candidate, Requirement

# Import from our copied resolvelib (same package layout)
from pipstyle.resolvelib.providers import AbstractProvider
from pipstyle.resolvelib.structs import RequirementInformation


class DBProvider(AbstractProvider[Requirement, Candidate, int]):
    """
    Provider that uses ResolutionContext (in-memory + LRU chunks) for candidate
    discovery. Identifier KT = name_id (int). Root pinning and time/Python
    filtering are applied in find_matches.
    """

    def __init__(
        self,
        ctx: ResolutionContext,
        start_node_id: int,
        root_node_id: int,
        root_name_id: int,
        t: int,
    ):
        self._ctx = ctx
        self._start_node_id = start_node_id
        self._root_node_id = root_node_id
        self._root_name_id = root_name_id
        self._t = t
        self._state_mapping: Optional[Dict[int, Candidate]] = None  # set via set_state

    def set_state(self, state: Any) -> None:
        """Optional hook: store current resolution state for Python mask filtering."""
        self._state_mapping = getattr(state, "mapping", None)

    def identify(self, requirement_or_candidate: Requirement | Candidate) -> int:
        if isinstance(requirement_or_candidate, Requirement):
            return requirement_or_candidate.name_id
        return requirement_or_candidate.name_id

    def _allowed_py_mask(self) -> int:
        """Intersection of py_mask over currently pinned candidates. 0 means no constraint."""
        if not self._state_mapping:
            return (1 << 26) - 1  # all bits
        mask = (1 << 26) - 1
        node_py_mask = self._ctx.node_py_mask
        for cand in self._state_mapping.values():
            if cand.node_id < len(node_py_mask):
                mask &= node_py_mask[cand.node_id]
            if mask == 0:
                break
        return mask

    def find_matches(
        self,
        identifier: int,
        requirements: Mapping[int, Iterator[Requirement]],
        incompatibilities: Mapping[int, Iterator[Candidate]],
    ):
        name_id = identifier
        req_iter = requirements.get(name_id)
        if req_iter is None:
            return iter([])

        reqs = list(req_iter)
        incompat_set: Set[int] = set()
        inc_iter = incompatibilities.get(name_id)
        if inc_iter is not None:
            for c in inc_iter:
                incompat_set.add(c.node_id)

        # Root requirement (parent is None) -> only start_node_id is allowed
        has_root_requirement = any(r.parent is None for r in reqs)
        if has_root_requirement:
            allowed = {self._start_node_id}
        elif name_id == self._root_name_id:
            # Root pinning: only root_node_id
            if self._root_node_id < len(self._ctx.node_time):
                tm = self._ctx.node_time[self._root_node_id]
                if tm is not None and tm <= self._t:
                    allowed = {self._root_node_id}
                else:
                    allowed = set()
            else:
                allowed = set()
        else:
            # Collect parent node_ids and intersect candidate sets
            parent_node_ids: List[int] = []
            for r in reqs:
                if r.parent is not None:
                    parent_node_ids.append(r.parent.node_id)
            if not parent_node_ids:
                allowed = set()
            else:
                allowed = None
                for src_id in parent_node_ids:
                    cands = set(
                        iter_candidates_newest_first(
                            self._ctx,
                            src_id,
                            name_id,
                            self._t,
                            self._root_name_id,
                            self._root_node_id,
                        )
                    )
                    if allowed is None:
                        allowed = cands
                    else:
                        allowed &= cands
                if allowed is None:
                    allowed = set()

        allowed -= incompat_set
        allowed_py = self._allowed_py_mask()
        node_py_mask = self._ctx.node_py_mask
        node_time = self._ctx.node_time
        node_name_id = self._ctx.node_name_id

        # Filter by Python mask and build list; sort by time descending (newest first)
        valid: List[int] = []
        for nid in allowed:
            if nid >= len(node_time) or node_time[nid] is None or node_time[nid] > self._t:
                continue
            if nid < len(node_py_mask) and (node_py_mask[nid] & allowed_py) == 0:
                continue
            valid.append(nid)

        valid.sort(key=lambda n: node_time[n] if n < len(node_time) and node_time[n] is not None else 0, reverse=True)
        for nid in valid:
            name_id_val = node_name_id[nid] if nid < len(node_name_id) else name_id
            yield Candidate(node_id=nid, name_id=name_id_val)

    def is_satisfied_by(self, requirement: Requirement, candidate: Candidate) -> bool:
        if candidate.name_id != requirement.name_id:
            return False
        if requirement.parent is None:
            return candidate.node_id == self._start_node_id
        src_id = requirement.parent.node_id
        if self._root_name_id == requirement.name_id:
            return candidate.node_id == self._root_node_id
        return edge_exists_upto_t(self._ctx, src_id, requirement.name_id, candidate.node_id, self._t)

    def get_dependencies(self, candidate: Candidate) -> List[Requirement]:
        dep_name_ids = self._ctx.get_dep_name_ids(candidate.node_id)
        return [Requirement(name_id=dep_name_id, parent=candidate) for dep_name_id in dep_name_ids]

    def get_preference(
        self,
        identifier: int,
        resolutions: Mapping[int, Candidate],
        candidates: Mapping[int, Iterator[Candidate]],
        information: Mapping[int, Iterator[RequirementInformation[Requirement, Candidate]]],
        backtrack_causes: Sequence[RequirementInformation[Requirement, Candidate]],
    ):
        """Prefer lower identifier (arbitrary but deterministic)."""
        return identifier
