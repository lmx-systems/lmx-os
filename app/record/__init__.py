"""The discipline layer (`docs/ROADMAP_1.5.md` REC-1..5).

What was decided, what was seen when it was decided, and what happened
afterwards - kept apart from the code that does any of it, because a record
that the deciding code can edit is not a record.

Classified UNCLASSIFIED in `tests/test_architecture_boundaries.py`: it is not
an adapter and it is not dispatch. The dispatch engine hands it a plan; nothing
here ever hands the dispatch engine anything back.
"""
from app.record.cost import (
    DriverDayCost,
    OrderCost,
    driver_day_cost,
    record_driver_day_cost,
)
from app.record.decisions import (
    canonical_inputs_hash,
    freeze_plan_inputs,
    record_decision,
    replay_inputs,
)
from app.record.consequences import (
    CONSEQUENCES,
    close_consequence_windows,
    consequence_label,
    label_counts,
    late_orders_awaiting_judgement,
    record_consequence,
    record_silence,
)
from app.record.outcomes import (
    current_outcome,
    outcomes_for,
    record_delivery_outcome,
    record_outcome,
    supersede_outcome,
)
from app.record.linkage import (
    flag_duplicates_across_branches,
    flag_open_returns_on_visits,
    flag_repeat_visits,
    open_flags,
    resolve_flag,
    run_linkage_detectors,
)

__all__ = [
    "record_silence",
    "record_consequence",
    "late_orders_awaiting_judgement",
    "label_counts",
    "consequence_label",
    "close_consequence_windows",
    "CONSEQUENCES",
    "DriverDayCost",
    "OrderCost",
    "canonical_inputs_hash",
    "current_outcome",
    "flag_duplicates_across_branches",
    "flag_open_returns_on_visits",
    "flag_repeat_visits",
    "freeze_plan_inputs",
    "open_flags",
    "outcomes_for",
    "record_decision",
    "record_delivery_outcome",
    "record_outcome",
    "driver_day_cost",
    "record_driver_day_cost",
    "replay_inputs",
    "resolve_flag",
    "supersede_outcome",
    "run_linkage_detectors",
]