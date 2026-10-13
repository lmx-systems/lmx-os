"""The failed-delivery rule, branch by branch (`DEC-9`).

The rule is a pure function so that every case a dispatcher used to decide by
hand reads here in a line. Two properties matter more than any one branch: it
never cancels an order, and it never "returns" parts a failed pickup never
collected.
"""
import pytest

from app.delivery.resolution import CANCEL, REDELIVER, RETURN_TO_SHOP
from app.playbook.rules import (
    ACCESS_ISSUE,
    COD_DISPUTE,
    ESCALATE,
    PARTS_MISSING,
    REFUSED,
    SHOP_CLOSED,
    decide_failed_delivery,
)


def _call(reason, attempt=1, failed_at="dropoff", max_attempts=2):
    return decide_failed_delivery(reason, attempt=attempt, failed_at=failed_at, max_attempts=max_attempts)


@pytest.mark.parametrize(
    ("reason", "attempt", "failed_at", "action"),
    [
        (SHOP_CLOSED, 1, "dropoff", REDELIVER),
        (SHOP_CLOSED, 2, "dropoff", RETURN_TO_SHOP),
        (SHOP_CLOSED, 1, "pickup", REDELIVER),
        (SHOP_CLOSED, 2, "pickup", ESCALATE),
        (ACCESS_ISSUE, 1, "dropoff", REDELIVER),
        (ACCESS_ISSUE, 2, "dropoff", RETURN_TO_SHOP),
        (REFUSED, 1, "dropoff", RETURN_TO_SHOP),
        (REFUSED, 1, "pickup", ESCALATE),
        (PARTS_MISSING, 1, "pickup", REDELIVER),
        (PARTS_MISSING, 2, "pickup", ESCALATE),
        (PARTS_MISSING, 1, "dropoff", ESCALATE),
        (COD_DISPUTE, 1, "dropoff", ESCALATE),
        (None, 1, "dropoff", ESCALATE),
        ("SOMETHING_NEW", 1, "dropoff", ESCALATE),
    ],
)
def test_each_case(reason, attempt, failed_at, action):
    assert _call(reason, attempt, failed_at).action == action


def test_it_never_cancels():
    reasons = (SHOP_CLOSED, ACCESS_ISSUE, REFUSED, PARTS_MISSING, COD_DISPUTE, None)
    for reason in reasons:
        for attempt in (1, 2, 3, 5):
            for failed_at in ("pickup", "dropoff", None):
                assert _call(reason, attempt, failed_at).action != CANCEL


def test_a_failed_pickup_is_never_returned():
    """Nothing was collected, so there is nothing to send back."""
    for reason in (SHOP_CLOSED, ACCESS_ISSUE, REFUSED, PARTS_MISSING, COD_DISPUTE):
        for attempt in (1, 2, 3):
            assert _call(reason, attempt, "pickup").action != RETURN_TO_SHOP


def test_the_attempt_limit_is_the_settings():
    assert _call(SHOP_CLOSED, attempt=2, max_attempts=3).action == REDELIVER
    assert _call(SHOP_CLOSED, attempt=3, max_attempts=3).action == RETURN_TO_SHOP


def test_no_failed_stop_on_record_is_treated_as_a_drop_off():
    """An order failed with no failed stop found - a route purged, say. Retrying
    is safe either way; only the end differs, and a drop-off is the common case."""
    assert _call(SHOP_CLOSED, attempt=1, failed_at=None).action == REDELIVER


def test_every_call_says_why():
    assert "closed" in _call(SHOP_CLOSED).reason
    assert _call(COD_DISPUTE).reason.endswith("a person settles it.")
