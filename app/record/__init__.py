"""The discipline layer (`docs/ROADMAP_1.5.md` REC-1..5).

What was decided, what was seen when it was decided, and what happened
afterwards - kept apart from the code that does any of it, because a record
that the deciding code can edit is not a record.

Classified UNCLASSIFIED in `tests/test_architecture_boundaries.py`: it is not
an adapter and it is not dispatch. The dispatch engine hands it a plan; nothing
here ever hands the dispatch engine anything back.
"""
from app.record.decisions import (
    canonical_inputs_hash,
    freeze_plan_inputs,
    record_decision,
    replay_inputs,
)

__all__ = [
    "canonical_inputs_hash",
    "freeze_plan_inputs",
    "record_decision",
    "replay_inputs",
]
