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
from app.identity.node_class import (
    NODE_CLASSES,
    classification_coverage,
    classify_unlabelled_locations,
    infer_node_class,
    set_node_class,
)
from app.identity.inherited_dwell import (
    MIN_OWN_SAMPLES,
    DwellEstimate,
    ImportReport,
    dwell_estimate,
    import_inherited_dwell,
)
from app.identity.profile import (
    profile_for,
    refresh_dwell_statistics,
    refresh_hub_dwell_statistics,
    set_access,
    set_autonomy_fit,
    set_receiving_hours,
)
from app.identity.resolution import (
    canonical_location,
    receiver_key_for,
    link_shop_to_dock,
    resolve_location,
)

__all__ = [
    "DwellEstimate",
    "ImportReport",
    "MIN_OWN_SAMPLES",
    "dwell_estimate",
    "import_inherited_dwell",
    "NODE_CLASSES",
    "canonical_location",
    "classification_coverage",
    "classify_unlabelled_locations",
    "confirm_merge",
    "infer_node_class",
    "merge_locations",
    "pending_merges",
    "profile_for",
    "propose_duplicate_locations",
    "propose_merge",
    "refresh_dwell_statistics",
    "refresh_hub_dwell_statistics",
    "reject_merge",
    "receiver_key_for",
    "link_shop_to_dock",
    "resolve_location",
    "set_access",
    "set_autonomy_fit",
    "set_node_class",
    "set_receiving_hours",
    "revert_merge",
]
