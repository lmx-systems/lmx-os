"""
Coverage for the accept-gate (docs/ROADMAP.md G4) and the marginal cost
model behind it (G7).

The behaviours worth protecting:

- Checks run cheapest-first and short-circuit. An offer rejected for being
  unreachable must never have been costed, because the whole point of the
  ordering is that a 45-second offer window doesn't allow computing
  economics for a job that was never feasible.
- Deadhead changes answers. A job that looks fine on headline pay is a loss
  once the unpaid drive to the pickup is counted - that is the entire
  premise of G7, and the pilot's $70.74/hr figure excludes exactly this.
- A commitment already made is never broken for money. A missed window is a
  Service Failure on the driver's own platform account (G13), so the gate
  refuses regardless of pay.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.gig_platform.accept_gate import (
    ACCEPTABLE,
    BREAKS_COMMITMENT,
    IMPOSSIBLE_WINDOWS,
    NOT_SEQUENCEABLE,
    OVER_CAPACITY,
    UNPROFITABLE,
    UNREACHABLE,
    evaluate_offer,
)
from app.gig_platform.economics import evaluate_marginal_economics
from app.models.gig_job import GigJob

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 4, 14, 0, tzinfo=timezone.utc)

# Austin. Driver sits downtown; these are all a few miles apart.
DRIVER = (30.2672, -97.7431)
NEAR_PICKUP = (30.2700, -97.7400)      # ~0.3 mi from the driver
NEAR_DROPOFF = (30.2750, -97.7350)     # ~0.5 mi further
FAR_PICKUP = (30.4500, -97.6000)       # ~15 mi away


def _job(
    *,
    pickup=NEAR_PICKUP,
    dropoff=NEAR_DROPOFF,
    pay_cents=2517,
    pickup_open_min=0,
    pickup_close_min=70,
    dropoff_close_min=180,
    status="offered",
    ref="S4588150.002-HOU1",
) -> GigJob:
    return GigJob(
        id=uuid.uuid4(),
        hub_id=uuid.uuid4(),
        driver_id=uuid.uuid4(),
        source_platform="dispatch",
        intake_source="manual",
        platform_job_ref=ref,
        pickup_address="pickup",
        pickup_lat=pickup[0] if pickup else None,
        pickup_lng=pickup[1] if pickup else None,
        dropoff_address="dropoff" if dropoff else None,
        dropoff_lat=dropoff[0] if dropoff else None,
        dropoff_lng=dropoff[1] if dropoff else None,
        pickup_window_open=NOW + timedelta(minutes=pickup_open_min),
        pickup_window_close=NOW + timedelta(minutes=pickup_close_min),
        dropoff_window_open=NOW + timedelta(minutes=pickup_open_min + 20),
        dropoff_window_close=NOW + timedelta(minutes=dropoff_close_min),
        pay_cents=pay_cents,
        status=status,
    )


def _evaluate(offer, committed=None, capacity_units=5):
    return evaluate_offer(
        offer=offer,
        driver_lat=DRIVER[0],
        driver_lng=DRIVER[1],
        committed=committed or [],
        capacity_units=capacity_units,
        now=NOW,
    )


# --------------------------------------------------------------------------
# 1. Reachability
# --------------------------------------------------------------------------


def test_a_nearby_job_with_a_wide_window_is_accepted():
    verdict = _evaluate(_job())
    assert verdict.accept is True
    assert verdict.reason == ACCEPTABLE
    assert verdict.economics is not None


def test_the_screenshotted_offer_is_rejected_by_the_very_first_check():
    """The one real offer we have: surfaced with four minutes left of a
    seventy-minute pickup window, fifteen miles away. The roadmap notes step
    1 alone rejects it, and that has to stay true."""
    verdict = _evaluate(_job(pickup=FAR_PICKUP, pickup_close_min=4))
    assert verdict.accept is False
    assert verdict.reason == UNREACHABLE
    # Never costed - the ordering exists so this work is skipped.
    assert verdict.economics is None


def test_an_offer_with_no_pickup_coordinates_is_unreachable_not_crashing():
    verdict = _evaluate(_job(pickup=(None, None)))
    assert verdict.accept is False
    assert verdict.reason == UNREACHABLE


# --------------------------------------------------------------------------
# 2. Self-consistency
# --------------------------------------------------------------------------


def test_a_dropoff_deadline_that_cannot_fit_the_delivery_leg_is_impossible():
    """Nobody could complete this one, whatever else is going on."""
    verdict = _evaluate(
        _job(pickup=NEAR_PICKUP, dropoff=FAR_PICKUP, pickup_open_min=0, dropoff_close_min=5)
    )
    assert verdict.accept is False
    assert verdict.reason == IMPOSSIBLE_WINDOWS
    assert verdict.economics is None


def test_a_collapsed_card_capture_says_it_cannot_tell_rather_than_guessing():
    """A share-sheet capture of a collapsed card has no dropoff (G2). It got
    past reachability, but placing or pricing it is not possible - and
    guessing either way would be worse than saying so."""
    verdict = _evaluate(_job(dropoff=(None, None)))
    assert verdict.accept is False
    assert verdict.reason == NOT_SEQUENCEABLE
    assert "expand the offer card" in verdict.detail


# --------------------------------------------------------------------------
# 3. Placement against commitments (the G13 hard stop)
# --------------------------------------------------------------------------


def test_an_offer_that_would_strand_a_committed_pickup_is_refused():
    committed = [
        _job(
            pickup=FAR_PICKUP,
            pickup_open_min=10,
            pickup_close_min=25,
            status="accepted",
            ref="COMMITTED-1",
        )
    ]
    verdict = _evaluate(_job(dropoff=FAR_PICKUP, pickup_close_min=60), committed=committed)
    assert verdict.accept is False
    assert verdict.reason == BREAKS_COMMITMENT
    assert "COMMITTED-1" in verdict.detail


def test_a_lucrative_offer_still_loses_to_an_existing_commitment():
    """Money never overrides this check. A missed window costs the driver
    their platform standing, and with three drivers one deactivation is a
    third of capacity."""
    committed = [
        _job(
            pickup=FAR_PICKUP, pickup_open_min=10, pickup_close_min=25,
            status="accepted", ref="COMMITTED-1",
        )
    ]
    verdict = _evaluate(
        _job(dropoff=FAR_PICKUP, pickup_close_min=60, pay_cents=50_000),
        committed=committed,
    )
    assert verdict.accept is False
    assert verdict.reason == BREAKS_COMMITMENT


def test_an_already_delivered_job_does_not_constrain_anything():
    committed = [
        _job(
            pickup=FAR_PICKUP, pickup_open_min=10, pickup_close_min=25,
            status="delivered", ref="DONE-1",
        )
    ]
    assert _evaluate(_job(), committed=committed).accept is True


# --------------------------------------------------------------------------
# 4. Capacity
# --------------------------------------------------------------------------


def test_a_full_vehicle_rejects_the_offer():
    committed = [
        _job(status="picked_up", pickup_close_min=600, ref=f"HELD-{i}") for i in range(3)
    ]
    verdict = _evaluate(_job(), committed=committed, capacity_units=3)
    assert verdict.accept is False
    assert verdict.reason == OVER_CAPACITY


# --------------------------------------------------------------------------
# 5. Marginal economics (G7)
# --------------------------------------------------------------------------


def test_deadhead_turns_a_respectable_looking_fare_into_a_loss():
    """The whole premise of G7. At the pilot's median fare, a job fifteen
    miles away is a loss - and the headline $/mile and $/hour figures would
    both have called it fine."""
    verdict = _evaluate(
        _job(pickup=FAR_PICKUP, dropoff=FAR_PICKUP, pay_cents=2517, pickup_close_min=600)
    )
    assert verdict.accept is False
    assert verdict.reason == UNPROFITABLE
    assert verdict.economics is not None
    assert verdict.economics.deadhead_miles > 10


def test_the_same_job_close_by_is_profitable():
    """Control for the test above: identical pay, no deadhead."""
    verdict = _evaluate(_job(pay_cents=2517))
    assert verdict.accept is True
    assert verdict.economics.margin_cents > 0


def test_effective_hourly_sits_below_the_engaged_only_headline():
    """The pilot reported $70.74/hr engaged. Counting the unpaid legs must
    produce a lower number - if it ever doesn't, the model has stopped
    charging for deadhead."""
    economics = evaluate_marginal_economics(
        pay_cents=2517,
        driver_lat=DRIVER[0], driver_lng=DRIVER[1],
        pickup_lat=FAR_PICKUP[0], pickup_lng=FAR_PICKUP[1],
        dropoff_lat=NEAR_DROPOFF[0], dropoff_lng=NEAR_DROPOFF[1],
    )
    assert economics.deadhead_miles > 0
    assert economics.effective_hourly_cents < 7074


def test_the_unpaid_legs_are_both_counted():
    economics = evaluate_marginal_economics(
        pay_cents=5000,
        driver_lat=DRIVER[0], driver_lng=DRIVER[1],
        pickup_lat=NEAR_PICKUP[0], pickup_lng=NEAR_PICKUP[1],
        dropoff_lat=FAR_PICKUP[0], dropoff_lng=FAR_PICKUP[1],
    )
    assert economics.deadhead_miles > 0
    assert economics.engaged_miles > 0
    # Charged back toward where the driver started, at the partial rate.
    assert economics.reposition_miles > 0
    assert economics.total_cost_cents == economics.vehicle_cost_cents + economics.time_cost_cents


def test_a_zero_pay_job_is_never_profitable():
    verdict = _evaluate(_job(pay_cents=0))
    assert verdict.accept is False
    assert verdict.reason == UNPROFITABLE
