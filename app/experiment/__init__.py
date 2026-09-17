"""The control arm and the guards around it (`docs/ROADMAP_1.5.md` EXP-1..3).

`EXP-1` is what turns the central claim from modelled to observed. A cost-per-
drop delta computed from the order book is a projection of our own assumptions;
one measured against 5-10% of orders dispatched exactly as the customer would
have is evidence. Everything Phase 2 exists to prove rests on this being done
properly - and "properly" here is mostly about restraint, because an arm that
can be re-rolled, skewed or quietly switched off proves nothing at all.

Classified UNCLASSIFIED in `tests/test_architecture_boundaries.py`: it is not
an adapter, and it is not dispatch. It labels an order at intake and the
dispatch engine reads that label; nothing here plans a route.
"""
from app.experiment.arms import (
    ARM_CONTROL,
    ARM_TREATMENT,
    MAX_CONTROL_FRACTION,
    MIN_CONTROL_FRACTION,
    STRATUM_UNKNOWN_RECEIVER,
    ArmNotContractedError,
    arm_for_order,
    assign_arm,
    assignment_for,
    block_size,
    control_arm_is_live,
    verify_assignment,
)
from app.experiment.exclusions import (
    ExclusionImpact,
    ReceiverExcluded,
    exclude_receiver,
    exclusion_impact,
    is_excluded,
    revoke_exclusion,
)

__all__ = [
    "ARM_CONTROL",
    "ARM_TREATMENT",
    "MAX_CONTROL_FRACTION",
    "MIN_CONTROL_FRACTION",
    "STRATUM_UNKNOWN_RECEIVER",
    "ArmNotContractedError",
    "ExclusionImpact",
    "ReceiverExcluded",
    "arm_for_order",
    "assign_arm",
    "assignment_for",
    "block_size",
    "control_arm_is_live",
    "exclude_receiver",
    "exclusion_impact",
    "is_excluded",
    "revoke_exclusion",
    "verify_assignment",
]
