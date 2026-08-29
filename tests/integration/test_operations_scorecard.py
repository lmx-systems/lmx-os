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


# ---------------------------------------------------------------------------
# The driver's own view (docs/ROADMAP.md W4)
# ---------------------------------------------------------------------------


async def _driver_in(db_session, hub_id, name="Extra"):
    driver_id = uuid.uuid4()
    db_session.add(
        Driver(
            id=driver_id,
            hub_id=hub_id,
            name=name,
            phone=f"+1512555{uuid.uuid4().int % 9000:04d}",
        )
    )
    await db_session.commit()
    return driver_id


async def _colleagues(db_session, hub_id, *, how_many, start):
    """`how_many` other drivers, each with a finished shift and some deliveries."""
    ids = []
    for i in range(how_many):
        other = await _driver_in(db_session, hub_id, name=f"Colleague {i}")
        await _shift(db_session, other, hub_id, start=start, end=start + timedelta(hours=2))
        await _delivered_stops(
            db_session, hub_id, other, count=2 + i, at=start + timedelta(hours=1)
        )
        ids.append(other)
    return ids


async def test_the_driver_sees_the_identical_computation_not_a_reduced_one(db_session):
    """The requirement W4 actually states, asserted rather than claimed.

    The driver's own DPH must equal what the fleet-wide computation produces when
    narrowed to them - which is only guaranteed because there is one implementation and a
    filter, not two functions kept in step by hand.
    """
    from app.reporting.operations import _deliveries_per_hour, build_driver_scorecard

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=2))
    await _delivered_stops(db_session, hub_id, driver_id, count=5, at=start + timedelta(hours=1))
    await _colleagues(db_session, hub_id, how_many=3, start=start)

    card = await build_driver_scorecard(
        db_session, driver_id=driver_id, hub_id=hub_id, now=NOW
    )
    direct = await _deliveries_per_hour(
        db_session, NOW - timedelta(days=30), driver_id=driver_id, hub_id=hub_id
    )
    assert card.own_deliveries_per_hour.median == direct.median
    assert card.own_deliveries_per_hour.median == pytest.approx(2.5, abs=0.01)


async def test_the_fleet_median_includes_the_driver(db_session):
    """"Everyone except you" is a subtly adversarial number and not a shared standard.

    Four drivers at 2, 3, 4 and 5 deliveries over two hours each -> rates 1.0, 1.5, 2.0,
    2.5. A median including all four sits above one that dropped this driver's own 1.0,
    so the two are distinguishable.
    """
    from app.reporting.operations import build_driver_scorecard

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=2))
    await _delivered_stops(db_session, hub_id, driver_id, count=2, at=start + timedelta(hours=1))
    await _colleagues(db_session, hub_id, how_many=3, start=start)

    card = await build_driver_scorecard(
        db_session, driver_id=driver_id, hub_id=hub_id, now=NOW
    )
    assert card.fleet_deliveries_per_hour is not None
    assert card.fleet_deliveries_per_hour.sample_size == 4, "all four driver-days counted"


async def test_the_comparison_is_withheld_when_it_would_point_at_one_person(db_session):
    """A privacy guard between colleagues, not a statistical one.

    With one other driver, the team median plus your own figure IS that person's figure -
    so showing it turns a trust feature into a way to read a colleague's performance.
    """
    from app.reporting.operations import build_driver_scorecard

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=2))
    await _delivered_stops(db_session, hub_id, driver_id, count=4, at=start + timedelta(hours=1))
    await _colleagues(db_session, hub_id, how_many=1, start=start)

    card = await build_driver_scorecard(
        db_session, driver_id=driver_id, hub_id=hub_id, now=NOW
    )
    assert card.fleet_deliveries_per_hour is None
    assert card.fleet_eta_error is None
    assert "one person" in card.comparison_withheld

    # Their OWN numbers still show. Only the comparison is withheld.
    assert card.own_deliveries_per_hour.not_measured is None
    assert card.own_deliveries_per_hour.median == pytest.approx(2.0, abs=0.01)


async def test_the_comparison_appears_once_there_are_enough_colleagues(db_session):
    from app.reporting.operations import (
        MIN_OTHER_DRIVERS_FOR_COMPARISON,
        build_driver_scorecard,
    )

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=2))
    await _delivered_stops(db_session, hub_id, driver_id, count=4, at=start + timedelta(hours=1))
    await _colleagues(
        db_session, hub_id, how_many=MIN_OTHER_DRIVERS_FOR_COMPARISON, start=start
    )

    card = await build_driver_scorecard(
        db_session, driver_id=driver_id, hub_id=hub_id, now=NOW
    )
    assert card.comparison_withheld is None
    assert card.fleet_deliveries_per_hour is not None


async def test_a_driver_never_sees_another_hubs_numbers(db_session):
    """Density and geography differ between hubs, so comparing across them is not a
    shared standard - it is a comparison to a different job."""
    from app.reporting.operations import build_driver_scorecard

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    other_hub, _, _, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)

    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=2))
    await _delivered_stops(db_session, hub_id, driver_id, count=2, at=start + timedelta(hours=1))
    # A busy crowd at the other hub - enough to satisfy the threshold if it leaked in.
    await _colleagues(db_session, other_hub, how_many=5, start=start)

    card = await build_driver_scorecard(
        db_session, driver_id=driver_id, hub_id=hub_id, now=NOW
    )
    assert card.comparison_withheld is not None, "the other hub's drivers must not count"


async def test_a_new_driver_sees_an_honest_refusal_not_a_zero(db_session):
    """Someone who has not finished a shift yet has no DPH.

    Reporting 0.0 would tell a brand-new driver they are performing at zero, which is
    both false and exactly the reading W4 is trying to avoid.
    """
    from app.reporting.operations import build_driver_scorecard

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _colleagues(db_session, hub_id, how_many=3, start=start)

    card = await build_driver_scorecard(
        db_session, driver_id=driver_id, hub_id=hub_id, now=NOW
    )
    assert card.own_deliveries_per_hour.median is None
    assert "for you" in card.own_deliveries_per_hour.not_measured


async def test_the_endpoint_takes_the_driver_from_the_token(db_session, real_redis_client):
    """There is deliberately no way to ask for somebody else's scorecard.

    Same rule the public order API follows for deriving a client from its key rather than
    from the request.
    """
    import inspect

    from app.api.driver_routes import get_my_scorecard

    params = set(inspect.signature(get_my_scorecard).parameters)
    assert "driver_id" not in params, "the driver must come from the token, not a parameter"
    assert "driver" in params

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    start = NOW - timedelta(days=1)
    await _shift(db_session, driver_id, hub_id, start=start, end=start + timedelta(hours=2))
    await _delivered_stops(db_session, hub_id, driver_id, count=4, at=start + timedelta(hours=1))

    from app.driver_auth.dependencies import AuthedDriver

    view = await get_my_scorecard(
        driver=AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="d"),
        session=db_session,
    )
    assert [m.name for m in view.metrics] == [
        "Deliveries per hour",
        "ETA error (actual minus predicted)",
    ]
    # Two drivers is not enough for a comparison, so it is withheld here.
    assert view.comparison_withheld is not None
    assert all(m.fleet_median is None for m in view.metrics)


async def test_ratings_and_flag_rates_are_absent_from_the_driver_view(db_session):
    """Deliberate, and worth pinning down so it is not added casually.

    Both exist per driver. A recipient rating shown back to a driver is the sharpest edge
    in this data - one bad rating lands hard, and recipients rate for reasons outside a
    driver's control. Adding either belongs to a decision, not to a refactor.
    """
    from app.reporting.operations import build_driver_scorecard

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    card = await build_driver_scorecard(
        db_session, driver_id=driver_id, hub_id=hub_id, now=NOW
    )
    fields = set(card.__dataclass_fields__)
    assert not {f for f in fields if "rating" in f or "flag" in f}


# ---------------------------------------------------------------------------
# The client's own view (docs/ROADMAP.md F7)
# ---------------------------------------------------------------------------


async def test_a_client_sees_only_their_own_deliveries(db_session):
    """The scoping property, and the one that would be worst to get wrong.

    A distributor reading another distributor's volume is a disclosure about a customer,
    not a reporting bug.
    """
    from app.reporting.operations import build_client_performance

    hub_id, client_id, _, shop_id = await _hub_client_driver(db_session)
    other_hub, other_client, _, other_shop = await _hub_client_driver(db_session)

    db_session.add(
        ClientSlaTerm(
            client_id=client_id, sla_tier="T2", delivery_target_minutes=180, credit_percent=25
        )
    )
    await db_session.commit()

    await _delivered_order(db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=-10)
    for _ in range(5):
        await _delivered_order(
            db_session, other_hub, other_client, other_shop, tier="T2", late_by_minutes=-10
        )

    mine = await build_client_performance(db_session, client_id=client_id, now=NOW)
    assert mine.delivered_count == 1, "the other client's five must not appear"


async def test_the_client_figure_equals_the_one_billing_credits_against(db_session):
    """One computation, three readers.

    The client's dashboard, our dashboard and their invoice all resolve on-time through
    `app/sla/commitment.py`. This asserts the first two agree by construction - a client
    must never be looking at 98% while being credited for a breach.
    """
    from app.billing.credits import assess_credits
    from app.reporting.operations import build_client_performance

    hub_id, client_id, _, shop_id = await _hub_client_driver(db_session)
    db_session.add(
        ClientSlaTerm(
            client_id=client_id, sla_tier="T2", delivery_target_minutes=180, credit_percent=25
        )
    )
    await db_session.commit()

    on_time = await _delivered_order(
        db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=-20
    )
    late = await _delivered_order(
        db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=60
    )
    late.fee_cents = 1_800
    await db_session.commit()

    perf = await build_client_performance(db_session, client_id=client_id, now=NOW)
    rate = next(r for r in perf.hit_rates if "T2" in r.name)
    assert (rate.numerator, rate.denominator) == (1, 2)

    # And billing agrees about which one was late.
    assessment = await assess_credits(
        db_session, client_id=client_id, orders=[on_time, late]
    )
    assert len(assessment.breaches) == 1
    assert assessment.breaches[0].order.id == late.id


async def test_volume_is_reported_even_when_on_time_cannot_be(db_session):
    """"We delivered 3 things for you and cannot yet say how many were on time" is a
    coherent answer. Suppressing both would look like an outage."""
    from app.reporting.operations import build_client_performance

    hub_id, client_id, _, shop_id = await _hub_client_driver(db_session)
    # Deliberately no ClientSlaTerm, so nothing is assessable.
    for _ in range(3):
        await _delivered_order(
            db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=5
        )

    perf = await build_client_performance(db_session, client_id=client_id, now=NOW)
    assert perf.delivered_count == 3
    assert all(r.not_measured for r in perf.hit_rates)


async def test_a_client_with_no_deliveries_gets_a_reason_not_a_zero_percent(db_session):
    """Showing "0% on time" to a client who has sent us nothing would be a false
    accusation against ourselves, and the kind of number that gets screenshotted."""
    from app.reporting.operations import build_client_performance

    _, client_id, _, _ = await _hub_client_driver(db_session)
    perf = await build_client_performance(db_session, client_id=client_id, now=NOW)

    assert perf.delivered_count == 0
    assert all(r.percentage is None and r.not_measured for r in perf.hit_rates)


async def test_the_endpoint_takes_the_client_from_the_token(db_session, real_redis_client):
    """No parameter can name another distributor."""
    import inspect

    from app.api.client_routes import my_performance
    from app.client_auth.dependencies import AuthedClient
    from app.models.client_user import CLIENT_MEMBER_ROLE

    params = set(inspect.signature(my_performance).parameters)
    assert "client_id" not in params
    assert "client" in params

    hub_id, client_id, _, shop_id = await _hub_client_driver(db_session)
    db_session.add(
        ClientSlaTerm(
            client_id=client_id, sla_tier="T2", delivery_target_minutes=180, credit_percent=25
        )
    )
    await db_session.commit()
    await _delivered_order(db_session, hub_id, client_id, shop_id, tier="T2", late_by_minutes=-5)

    # A member, not an admin: a counter person fielding "are they any good" needs this.
    view = await my_performance(
        client=AuthedClient(
            client_id=str(client_id),
            client_user_id=str(uuid.uuid4()),
            email="counter@example.com",
            name="Alex",
            role=CLIENT_MEMBER_ROLE,
        ),
        session=db_session,
    )
    assert view.delivered_count == 1
    assert next(r for r in view.hit_rates if "T2" in r.name).percentage == 100.0


async def test_the_tier_label_survives_both_forms_of_sla_tier(db_session):
    """`Order.sla_tier` is an SLATier member when loaded from Postgres and a plain string
    on an object built in Python and not reloaded.

    Both compare and hash the same because SLATier subclasses str, so the difference is
    invisible until an f-string renders the member as "SLATier.T2" - or until `.value`
    is called on the string and raises. This caught the second case in a test that passed
    an in-memory order straight to the scorecard.
    """
    from app.models.order import SLATier
    from app.reporting.operations import _tier_label

    assert _tier_label(SLATier.T2) == "T2"
    assert _tier_label("T2") == "T2"
    assert _tier_label(None) == "unspecified"


# ---------------------------------------------------------------------------
# Offer outcomes and decline reasons (docs/ROADMAP.md I1 -> I4)
# ---------------------------------------------------------------------------


async def _offer(db_session, hub_id, driver_id, *, status, reason=None, offered_ago_hours=1):
    from app.models.route_offer import RouteOffer

    at = NOW - timedelta(hours=offered_ago_hours)
    offer = RouteOffer(
        hub_id=hub_id,
        driver_id=driver_id,
        status=status,
        stop_payload=[],
        offered_at=at,
        expires_at=at + timedelta(minutes=2),
        responded_at=at + timedelta(seconds=20) if status != "offered" else None,
        decline_reason=reason,
    )
    db_session.add(offer)
    await db_session.commit()
    return offer


async def test_declined_and_expired_are_reported_separately(db_session):
    """They mean opposite things and get confused.

    A decline is an answer - the driver saw the work and did not want it. An expiry is
    silence, most likely because they never saw the offer at all, which is the likelier
    failure while push notifications are stubbed. A high decline rate is a pricing or
    routing problem; a high expiry rate is a delivery problem. Reading one as the other
    sends you to fix the wrong thing.
    """
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    await _offer(db_session, hub_id, driver_id, status="accepted")
    await _offer(db_session, hub_id, driver_id, status="declined", reason="too_far")
    await _offer(db_session, hub_id, driver_id, status="expired")
    await _offer(db_session, hub_id, driver_id, status="expired")

    scorecard = await build_operations_scorecard(db_session, now=NOW)
    accepted = _find(scorecard, "Offers accepted")
    declined = _find(scorecard, "Offers declined")
    expired = _find(scorecard, "expired unanswered")

    assert (accepted.numerator, accepted.denominator) == (1, 4)
    assert (declined.numerator, declined.denominator) == (1, 4)
    assert (expired.numerator, expired.denominator) == (2, 4)


async def test_decline_reasons_are_rated_over_declines_not_over_all_offers(db_session):
    """Otherwise the reasons look rarer the more offers get accepted, which is backwards -
    the population that could have given a reason is the declines."""
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    for _ in range(6):
        await _offer(db_session, hub_id, driver_id, status="accepted")
    await _offer(db_session, hub_id, driver_id, status="declined", reason="too_far")
    await _offer(db_session, hub_id, driver_id, status="declined", reason="too_far")
    await _offer(db_session, hub_id, driver_id, status="declined", reason="pay_too_low")

    scorecard = await build_operations_scorecard(db_session, now=NOW)
    too_far = _find(scorecard, "Declined: too_far")
    assert (too_far.numerator, too_far.denominator) == (2, 3), "3 declines, not 9 offers"
    assert too_far.percentage == pytest.approx(66.7, abs=0.1)


async def test_declines_without_a_reason_are_reported_as_such(db_session):
    """The prompt is optional and appears after the decline, so "nobody has said yet" is
    the expected state - and it is a different message from "nobody declines"."""
    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    await _offer(db_session, hub_id, driver_id, status="declined")
    await _offer(db_session, hub_id, driver_id, status="declined")

    scorecard = await build_operations_scorecard(db_session, now=NOW)
    given = _find(scorecard, "Declines with a reason")
    assert given.not_measured is not None
    assert "optional" in given.not_measured


async def test_no_offers_reports_a_reason_rather_than_zero_percent(db_session):
    scorecard = await build_operations_scorecard(db_session, now=NOW)
    assert _find(scorecard, "Offers accepted").not_measured is not None


# ---------------------------------------------------------------------------
# The endpoint that records a reason
# ---------------------------------------------------------------------------


async def test_a_reason_can_be_attached_after_the_decline(db_session, real_redis_client):
    """The whole design: the decline releases the work on one tap, and the reason arrives
    separately if the driver bothers."""
    from app.api.driver_routes import annotate_decline_reason
    from app.driver_auth.dependencies import AuthedDriver
    from app.schemas.driver_app import DECLINE_TOO_FAR, DeclineOfferBody

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    offer = await _offer(db_session, hub_id, driver_id, status="declined")

    await annotate_decline_reason(
        offer_id=str(offer.id),
        body=DeclineOfferBody(reason=DECLINE_TOO_FAR),
        driver=AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="d"),
        session=db_session,
    )
    await db_session.refresh(offer)
    assert offer.decline_reason == DECLINE_TOO_FAR


async def test_a_reason_cannot_be_attached_to_an_expired_offer(db_session, real_redis_client):
    """An expiry means the driver never answered. A reason on it would be somebody
    claiming a decision they did not make."""
    from fastapi import HTTPException

    from app.api.driver_routes import annotate_decline_reason
    from app.driver_auth.dependencies import AuthedDriver
    from app.schemas.driver_app import DeclineOfferBody

    hub_id, _, driver_id, _ = await _hub_client_driver(db_session)
    offer = await _offer(db_session, hub_id, driver_id, status="expired")

    with pytest.raises(HTTPException) as exc:
        await annotate_decline_reason(
            offer_id=str(offer.id),
            body=DeclineOfferBody(reason="too_far"),
            driver=AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="d"),
            session=db_session,
        )
    assert exc.value.status_code == 409
    await db_session.refresh(offer)
    assert offer.decline_reason is None


async def test_declining_still_works_with_no_reason_at_all(db_session, real_redis_client):
    """The constraint that shaped this: a driver has about two minutes and the orders are
    held until they answer, so the decline path must not have grown a required field."""
    import inspect

    from app.api.driver_routes import decline_offer

    body = inspect.signature(decline_offer).parameters["body"]
    assert body.default is None, "a reason must never be required to decline"
