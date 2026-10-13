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


# The unplaced rule (`DEC-10`).

from app.playbook.rules import OFFER, Candidate, decide_unplaced  # noqa: E402


def _c(driver_id, miles, stale=False):
    return Candidate(driver_id=driver_id, name=f"Driver {driver_id}", miles=miles, position_stale=stale)


def test_the_nearest_reporting_driver_is_chosen():
    call = decide_unplaced([_c("far", 5.0), _c("near", 1.2), _c("nearest-but-quiet", 0.2, stale=True)], hub_closed=False)
    assert (call.action, call.target) == (OFFER, "near")
    assert "1.2 miles" in call.reason


def test_when_every_app_is_quiet_the_nearest_is_named_to_phone_first():
    call = decide_unplaced([_c("a", 3.0, stale=True), _c("b", 0.5, stale=True)], hub_closed=False)
    assert (call.action, call.target) == (OFFER, "b")
    assert "phone them" in call.reason


def test_a_driver_with_no_position_comes_last():
    call = decide_unplaced([_c("unknown", None), _c("known", 9.0)], hub_closed=False)
    assert call.target == "known"


def test_nobody_free_or_a_closed_hub_goes_to_a_person():
    assert decide_unplaced([], hub_closed=False).action == ESCALATE
    assert decide_unplaced([_c("a", 1.0)], hub_closed=True).action == ESCALATE
    assert decide_unplaced([], hub_closed=False).target is None


# The step-up (`DEC-10`, D-BONUS).

from app.playbook.rules import OFFER_WITH_BONUS, OfferHistory  # noqa: E402


def _cd(driver_id, miles, *, declined=False, stale=False):
    return Candidate(
        driver_id=driver_id, name=f"Driver {driver_id}", miles=miles, position_stale=stale, turned_it_down=declined
    )


def test_turned_down_once_it_goes_again_with_the_bonus_to_someone_new():
    call = decide_unplaced(
        [_cd("decliner", 0.5, declined=True), _cd("new", 2.0)],
        hub_closed=False, history=OfferHistory(turned_down=1), bonus_cents=500,
    )
    assert (call.action, call.target) == (OFFER_WITH_BONUS, "new")
    assert "$5.00 bonus" in call.reason


def test_with_only_decliners_free_the_nearest_gets_the_bonus():
    call = decide_unplaced(
        [_cd("a", 3.0, declined=True), _cd("b", 1.0, declined=True)],
        hub_closed=False, history=OfferHistory(turned_down=2), bonus_cents=500,
    )
    assert (call.action, call.target) == (OFFER_WITH_BONUS, "b")
    assert "turned it down before" in call.reason


def test_turned_down_with_a_bonus_too_goes_to_a_person():
    call = decide_unplaced(
        [_cd("a", 1.0)], hub_closed=False, history=OfferHistory(turned_down=2, bonus_tried=True), bonus_cents=500
    )
    assert call.action == ESCALATE
