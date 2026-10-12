"""The console and the checklist baseline give reasons in the same words (DE-9, DE-10).

A scorer's reason codes are what the checklist's are scored against, and
`app/` and `ml/` share no code, so the two lists are kept in step here.
"""
import ml.modality.checklist as checklist
from app.models.machine_label import MISSING_DATA, NO_REASONS


def _codes(names):
    return {getattr(checklist, name) for name in names}


def test_every_checklist_reason_is_a_reason_a_scorer_can_give():
    no = _codes(
        ["TOO_HEAVY", "TOO_BIG", "HAZARDOUS", "NO_LEGAL_STOP", "STOP_TOO_FAR", "STEPS", "NO_SIDEWALK", "NO_SET_DOWN", "OBSTRUCTED"]
    )
    missing = _codes(
        ["WEIGHT_UNKNOWN", "SIZE_UNKNOWN", "HAZMAT_UNKNOWN", "STOP_POINT_UNKNOWN", "DOOR_UNKNOWN", "HOURS_UNKNOWN"]
    )

    assert no <= set(NO_REASONS)
    assert missing == set(MISSING_DATA)
