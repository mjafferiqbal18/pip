"""
Minimal Requirement and Candidate types for the DB-backed resolver.
Identifier (KT) = name_id (int). Resolvelib supplies State, Criterion, Result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Requirement: name_id + parent (Candidate or None for root requirement)
# Candidate: node_id + name_id (and optional py_mask for provider use)


@dataclass(frozen=True)
class Candidate:
    """A specific (package, version) identified by node_id."""

    node_id: int
    name_id: int

    def __hash__(self) -> int:
        return hash(self.node_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Candidate):
            return False
        return self.node_id == other.node_id


@dataclass(frozen=True)
class Requirement:
    """A dependency on a package name (name_id), requested by parent (Candidate or None)."""

    name_id: int
    parent: Optional[Candidate]  # None = root requirement (the package we are resolving for)

    def __hash__(self) -> int:
        return hash((self.name_id, self.parent.node_id if self.parent is not None else None))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Requirement):
            return False
        return self.name_id == other.name_id and self.parent == other.parent
