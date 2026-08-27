"""
Descriptive analytics on the captured truth (docs/ROADMAP.md I4).

`I1` added the fields that make operations measurable and **nothing read them.**
`Stop.planned_eta` in particular was added specifically so ETA accuracy could be scored,
and it was read in zero places - capturing truth nobody consumes is the same defect as
the ones that produced it.

The tests that carry the design:

  - **`test_eta_error_is_measured_against_planned_not_live_eta`.** The whole reason two
    ETA columns exist. `eta` is refreshed as the route progresses, so scoring against it
    measures the last few minutes of a route and reports near-perfect accuracy forever.
  - **`test_a_driver_still_on_shift_is_not_counted`.** Closing an open shift at `now`
    would divide today's handful of deliveries by a few minutes and produce a
    spectacular DPH figure - error in the direction that makes E9's assumption look
    validated when it is not.
  - **`test_an_order_with_no_commitment_is_excluded_not_counted_as_a_hit`.** `W11`
    established that "nobody wrote down what we owe this client" is not a time. It is
    equally not a success.
  - **`test_everything_refuses_to_answer_on_an_empty_window`.** The load-bearing
    behaviour: four reasons rather than four zeroes.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.client_sla_term import ClientSlaTerm
from app.models.driver import Driver
from app.models.driver_shift_event import DriverShiftEvent
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopFlag
from app.reporting.operations import (
    ASSUMED_DELIVERIES_PER_HOUR,
    build_operations_scorecard,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 20, 17, 0, tzinfo=timezone.utc)


def _find(scorecard, fragment):
    for item in [*scorecard.measurements, *scorecard.rates]:
        if fragment.lower() in item.name.lower():
            return item
    raise AssertionError(f"no metric matching {fragment!r}")


async def _hub_client_driver(db_session):
    hub_id, client_id, driver_id, shop_id = (uuid.uuid4() for _ in range(4))
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id,
            hub_id=hub_id,
            name="Design Partner",
            pos_system="client_portal",
            signup_status="active",
        )
    )
    db_session.add(
        Driver(
            id=driver_id,
            hub_id=hub_id,
            name="Sam O.",
            phone=f"+1512555{uuid.uuid4().int % 9000:04d}",
        )
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address="220 Harbor St",
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()
    return hub_id, client_id, driver_id, shop_id


async def _shift(db_session, driver_id, hub_id, *, start, end, kind="available"):
    db_session.add(
        DriverShiftEvent(
            driver_id=driver_id, hub_id=hub_id, event_type=kind, occurred_at=start
        )
    )
    if end is not None:
        db_session.add(
            DriverShiftEvent(
                driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=end
            )
        )
    await db_session.commit()


async def _delivered_stops(db_session, hub_id, driver_id, *, count, at, planned_eta=None, arrived_at=None):
    """`count` completed dropoffs on one route."""
    route = Route(hub_id=hub_id, driver_id=driver_id, status="completed")
    db_session.add(route)
    await db_session.flush()
    for i in range(count):
        db_session.add(
            Stop(
                route_id=route.id,
                shop_id=None,
                sequence=i,
                stop_type="dropoff",
                status="completed",
                parcel_count=1,
                completed_at=at,
                planned_eta=planned_eta,
                arrived_at=arrived_at,
            )
        )
    await db_session.commit()
    return route


# ---------------------------------------------------------------------------
# Refusing to answer
# ---------------------------------------------------------------------------


async def test_everything_refuses_to_answer_on_an_empty_window(db_session):
    """Four reasons, not four zeroes.

    This is the whole reporting vocabulary's reason to exist: a scorecard that prints 0%
    on an empty table gets quoted in an update, and nobody can tell it apart from a real
    zero.
    """
    scorecard = await build_operations_scorecard(db_session, now=NOW)

    for item in [*scorecard.measurements, *scorecard.rates]:
        assert item.not_measured, f"{item.name} should have refused"
    assert _find(scorecard, "deliveries per hour").median is None
    assert _find(scorecard, "hit rate").percentage is None


async def test_the_eta_refusal_says_what_would_fix_it(db_session):
    """"No data yet" and "nothing records this" need different actions, so the reason
    names which one it is and what produces the data."""
    scorecard = await build_operations_scorecard(db_session, now=NOW)
    reason = _find(scorecard, "ETA error").not_measured
    assert "planned_eta" in reason and "accepted" in reason


# ---------------------------------------------------------------------------
# Deliveries per hour (E9)
# ---------------------------------------------------------------------------


async def test_deliveries_per_hour_is_computed_from_real_shifts(db_session):
    """Four deliveries over a two-hour finished shift is 2.0 per hour."""
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=2))
    await _delivered_stops(db_session, hub_id, driver_id, count=4, at=start + timedelta(hours=1))

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "deliveries per hour")
    assert metric.not_measured is None
    assert metric.median == pytest.approx(2.0, abs=0.01)
    assert metric.unit == "deliveries/hour"
    assert str(ASSUMED_DELIVERIES_PER_HOUR) in metric.target


async def test_a_driver_still_on_shift_is_not_counted(db_session):
    """An unclosed interval is a driver working right now, not a completed shift.

    Closing it at `now` would divide the deliveries so far by however long they have been
    on - which for a shift that started twenty minutes ago is a spectacular DPH. That is
    error in the direction that makes E9's assumption look validated, which is the
    direction that matters.
    """
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    # On duty, never closed.
    await _shift(db_session, driver_id, hub_id, start=NOW - timedelta(minutes=20), end=None)
    await _delivered_stops(db_session, hub_id, driver_id, count=3, at=NOW - timedelta(minutes=5))

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "deliveries per hour")
    assert metric.not_measured is not None


async def test_en_route_counts_as_on_duty(db_session):
    """A driver carrying a route is working.

    Treating `en_route` as a gap would shrink the denominator and inflate DPH - again the
    flattering direction.
    """
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(
        db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=4), kind="en_route"
    )
    await _delivered_stops(db_session, hub_id, driver_id, count=4, at=start + timedelta(hours=1))

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "deliveries per hour")
    assert metric.median == pytest.approx(1.0, abs=0.01)


async def test_a_brief_toggle_is_not_a_shift(db_session):
    """Someone tapping available and off again is not a denominator.

    Ten minutes with one delivery would read as 6/hour and drag the median up.
    """
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(minutes=10))
    await _delivered_stops(db_session, hub_id, driver_id, count=1, at=start + timedelta(minutes=5))

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "deliveries per hour")
    assert metric.not_measured is not None


async def test_a_shift_with_no_deliveries_counts_as_zero_not_as_absent(db_session):
    """A driver who sat idle for four hours is data about DPH, not a missing row.

    Dropping those would measure "how productive are productive days", which is a
    different and much more flattering question.
    """
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=4))

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "deliveries per hour")
    assert metric.not_measured is None
    assert metric.median == 0.0
    assert metric.sample_size == 1


async def test_pickups_do_not_count_as_deliveries(db_session):
    """A pickup is work but it is not a delivery, and counting both would roughly double
    the number being compared against an assumption stated in deliveries."""
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=2))

    route = Route(hub_id=hub_id, driver_id=driver_id, status="completed")
    db_session.add(route)
    await db_session.flush()
    for i, kind in enumerate(("pickup", "pickup", "dropoff")):
        db_session.add(
            Stop(
                route_id=route.id,
                shop_id=None,
                sequence=i,
                stop_type=kind,
                status="completed",
                parcel_count=1,
                completed_at=start + timedelta(hours=1),
            )
        )
    await db_session.commit()

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "deliveries per hour")
    assert metric.median == pytest.approx(0.5, abs=0.01), "one dropoff over two hours"


# ---------------------------------------------------------------------------
# ETA accuracy
# ---------------------------------------------------------------------------


async def test_eta_error_is_measured_against_planned_not_live_eta(db_session):
    """The reason two ETA columns exist.

    `eta` is refreshed as the route progresses, so an arrival compared against it
    measures the last few minutes of a route and reports near-perfect accuracy forever.
    Here `planned_eta` is 20 minutes before the arrival while `eta` was refreshed to the
    arrival itself - so a correct implementation reports 20 minutes late and a wrong one
    reports zero.
    """
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    arrived = NOW - timedelta(hours=3)
    planned = arrived - timedelta(minutes=20)

    route = Route(hub_id=hub_id, driver_id=driver_id, status="completed")
    db_session.add(route)
    await db_session.flush()
    db_session.add(
        Stop(
            route_id=route.id,
            shop_id=None,
            sequence=0,
            stop_type="dropoff",
            status="completed",
            parcel_count=1,
            planned_eta=planned,
            eta=arrived,  # refreshed to the truth, as the live column is
            arrived_at=arrived,
            completed_at=arrived,
        )
    )
    await db_session.commit()

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "ETA error")
    assert metric.median == pytest.approx(20.0, abs=0.2), "must score against planned_eta"
    assert metric.unit == "minutes"


async def test_early_arrivals_report_negative_rather_than_absolute(db_session):
    """Early and late are different operational problems.

    Consistently early means the model is pessimistic and clients are being quoted worse
    than reality - not the same failure as being late. An absolute value would average
    the two into "accurate".
    """
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    arrived = NOW - timedelta(hours=3)
    await _delivered_stops(
        db_session,
        hub_id,
        driver_id,
        count=1,
        at=arrived,
        planned_eta=arrived + timedelta(minutes=15),
        arrived_at=arrived,
    )

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "ETA error")
    assert metric.median == pytest.approx(-15.0, abs=0.2)


async def test_a_stop_with_no_planned_eta_is_skipped(db_session):
    """Rather than counted as perfectly accurate. Stops created before migration 0040
    have no planned_eta, and treating a null as zero error would flatter the number with
    exactly the rows that carry no information."""
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    arrived = NOW - timedelta(hours=2)
    await _delivered_stops(
        db_session, hub_id, driver_id, count=3, at=arrived, planned_eta=None, arrived_at=arrived
    )

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "ETA error")
    assert metric.not_measured is not None


# ---------------------------------------------------------------------------
# Service-level hit rate
# ---------------------------------------------------------------------------


async def _delivered_order(db_session, hub_id, client_id, shop_id, *, tier, late_by_minutes):
    """An order delivered `late_by_minutes` after its tier commitment (negative = early)."""
    requested = NOW - timedelta(days=1)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
        source_system="client_portal",
        raw_payload={},
        sla_tier=tier,
        hold_deadline=requested,
        weight_units=1,
        status=OrderStatus.delivered,
        requested_at=requested,
        # The T2 term below is 180 minutes, so the commitment is requested + 3h.
        delivered_at=requested + timedelta(minutes=180 + late_by_minutes),
        delivery_address="900 Congress Ave",
    )
    db_session.add(order)
    await db_session.commit()
    return order


async def test_hit_rate_counts_on_time_against_the_billing_commitment(db_session):
    """The same function billing credits against.

    A hit rate with its own idea of the promise could report 98% while the invoice
    credited a breach, and nobody could say which was wrong.
    """
    hub_id, client_id, _, shop_id = await _hub_client_driver(db_session)
    db_session.add(
        ClientSlaTerm(
            client_id=client_id, sla_tier="T2", delivery_target_minutes=180, credit_percent=25
        )
    )
    await db_session.commit()

    await _delivered_order(db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=-30)
    await _delivered_order(db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=-5)
    await _delivered_order(db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=45)

    rate = _find(await build_operations_scorecard(db_session, now=NOW), "hit rate (T2)")
    assert (rate.numerator, rate.denominator) == (2, 3)
    assert rate.percentage == pytest.approx(66.7, abs=0.1)


async def test_a_thin_denominator_is_flagged_as_thin(db_session):
    """100% on one delivery is arithmetic, not information - so the percentage comes with
    its denominator and a flag, rather than standing alone in an update."""
    hub_id, client_id, _, shop_id = await _hub_client_driver(db_session)
    db_session.add(
        ClientSlaTerm(
            client_id=client_id, sla_tier="T2", delivery_target_minutes=180, credit_percent=25
        )
    )
    await db_session.commit()
    await _delivered_order(db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=-10)

    rate = _find(await build_operations_scorecard(db_session, now=NOW), "hit rate (T2)")
    assert rate.percentage == 100.0
    assert rate.is_thin is True
    assert rate.denominator == 1


async def test_an_order_with_no_commitment_is_excluded_not_counted_as_a_hit(db_session):
    """`W11` established that "nobody wrote down what we owe this client" is not a time.

    It is equally not a success. Counting it as one would make the hit rate improve every
    time a client was onboarded without terms.
    """
    hub_id, client_id, _, shop_id = await _hub_client_driver(db_session)
    # Deliberately no ClientSlaTerm.
    await _delivered_order(db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=999)

    scorecard = await build_operations_scorecard(db_session, now=NOW)
    rate = _find(scorecard, "hit rate")
    assert rate.not_measured is not None
    assert "no commitment on file" in rate.not_measured
    assert rate.percentage is None


# ---------------------------------------------------------------------------
# Hold-window effectiveness
# ---------------------------------------------------------------------------


async def test_hold_window_flags_are_rated_against_completed_deliveries(db_session):
    """The denominator is deliveries, not flags - a rate over flags is 100% by
    construction."""
    from app.learning_loop.detection import HOLD_TOO_SHORT_FLAG

    hub_id, client_id, driver_id, shop_id = await _hub_client_driver(db_session)
    at = NOW - timedelta(hours=4)
    route = await _delivered_stops(db_session, hub_id, driver_id, count=4, at=at)

    stop = (
        await db_session.execute(select(Stop).where(Stop.route_id == route.id).limit(1))
    ).scalars().first()
    db_session.add(
        StopFlag(
            stop_id=stop.id,
            flag_type=HOLD_TOO_SHORT_FLAG,
            created_by_driver_id=driver_id,
            note="shop wasn't ready",
        )
    )
    await db_session.commit()

    rate = _find(await build_operations_scorecard(db_session, now=NOW), "held wrong")
    assert (rate.numerator, rate.denominator) == (1, 4)
    assert rate.percentage == 25.0


async def test_an_unrelated_flag_does_not_count_as_a_hold_problem(db_session):
    """Only the two hold-window flags. A driver noting a gate code is useful annotation
    and says nothing about whether the window was right."""
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    at = NOW - timedelta(hours=4)
    route = await _delivered_stops(db_session, hub_id, driver_id, count=2, at=at)
    stop = (
        await db_session.execute(select(Stop).where(Stop.route_id == route.id).limit(1))
    ).scalars().first()
    db_session.add(
        StopFlag(
            stop_id=stop.id,
            flag_type="gate_code_needed",
            created_by_driver_id=driver_id,
        )
    )
    await db_session.commit()

    rate = _find(await build_operations_scorecard(db_session, now=NOW), "held wrong")
    assert rate.numerator == 0
    assert rate.denominator == 2


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


async def test_records_outside_the_window_are_excluded(db_session):
    """A 30-day window means 30 days. Otherwise "improving" is indistinguishable from
    "the bad month aged out"."""
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    ancient = NOW - timedelta(days=90)
    await _shift(db_session, driver_id, hub_id, start=ancient, end=ancient + timedelta(hours=2))
    await _delivered_stops(db_session, hub_id, driver_id, count=8, at=ancient + timedelta(hours=1))

    metric = _find(await build_operations_scorecard(db_session, now=NOW), "deliveries per hour")
    assert metric.not_measured is not None

    # And visible when the window is widened to include it.
    wide = _find(
        await build_operations_scorecard(db_session, window_days=120, now=NOW),
        "deliveries per hour",
    )
    assert wide.median == pytest.approx(4.0, abs=0.01)


async def test_the_scorecard_reports_its_own_window(db_session):
    """So a number can never be read without knowing what it covers."""
    scorecard = await build_operations_scorecard(db_session, window_days=7, now=NOW)
    assert scorecard.window_days == 7
    assert scorecard.window_start == NOW - timedelta(days=7)
    assert scorecard.generated_at == NOW
