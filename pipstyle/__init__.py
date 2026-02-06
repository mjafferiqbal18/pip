"""
pipstyle: DB-backed dependency resolution using pip's resolvelib.

Uses preprocessed MongoDB collections instead of PyPI for dependency resolution,
with optional root pinning and time-based candidate filtering.
"""

from pipstyle.entrypoint import ResolutionRunner, resolve_one
from pipstyle.loader import load_context, ResolutionContext

__all__ = [
    "ResolutionRunner",
    "ResolutionContext",
    "resolve_one",
    "load_context",
]
