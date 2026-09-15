"""Canonical identity for physical places (`docs/ROADMAP_1.5.md` IDN-1..4).

A Shop is a customer account; a Location is a physical dock. This package owns
the second of those and the resolution from an address to it.

Classified UNCLASSIFIED in `tests/test_architecture_boundaries.py` - see the
reason recorded there. In short: this is reference data, not an adapter, and
the dispatch engine is expected to read it once IDN-4 exists.
"""
from app.identity.merge import (
    confirm_merge,
    merge_locations,
    pending_merges,
    propose_duplicate_locations,
    propose_merge,
    reject_merge,
    revert_merge,
)
from app.identity.resolution import canonical_location, resolve_location

__all__ = [
    "canonical_location",
    "confirm_merge",
    "merge_locations",
    "pending_merges",
    "propose_duplicate_locations",
    "propose_merge",
    "reject_merge",
    "resolve_location",
    "revert_merge",
]
