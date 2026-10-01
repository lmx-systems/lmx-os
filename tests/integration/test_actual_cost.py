"""REC-2's last column: what a drop actually cost.

The property most of these defend is that **loaded costs sum back to the wage
bill**. A per-drop cost that does not is worse than none: it is smaller than
what we paid, it flatters every comparison built on it, and nothing downstream
can tell.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.driver import Driver
from app.models.driver_shift_event import DriverShiftEvent
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.outcome_entry import KIND_COST
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.models.stop_geofence_event import KIND_ENTER, KIND_EXIT, StopGeofenceEvent
from app.payroll.hours import PLACEHOLDER_HOURLY_RATE_CENTS
from app.record.cost import (
    RATE_FROM_DRIVER,
    RATE_PLACEHOLDER,
    SOURCE_GEOFENCE,
    SOURCE_TAPS,
    driver_day_cost,
    record_costs_for_period,
    stop_timings,
)
from app.record.outcomes import outcomes_for

pytestmark = pytest.mark.integration

DAY = datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc)
RATE = 3_000  # $30.00/hr, so an hour is a round number of cents


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Cost Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _driver(db_session, hub, *, rate: int | None = RATE) -> Driver:
    driver = Driver(
        hub_id=hub.id, name="Driver", phone=f"+1512555{uuid.uuid4().hex[:4]}",
        hourly_rate_cents=rate,
    )
    db_session.add(driver)
    await db_session.flush()
    return driver


async def _order(db_session, hub) -> Order:
    order = Order(
        hub_id=hub.id, external_order_ref=f"PO-{uuid.uuid4().hex[:8]}",
        source_system="flat_file", raw_payload={}, sla_tier="T2",
        status=OrderStatus.delivered, requested_at=DAY,
    )
    db_session.add(order)
    await db_session.flush()
    return order


async def _on_duty(db_session, hub, driver, start, end):
    """A shift the driver was on duty for, so paid time is real."""
    db_session.add(
        DriverShiftEvent(driver_id=driver.id, hub_id=hub.id,
                         event_type="available", occurred_at=start)
    )
    db_session.add(
        DriverShiftEvent(driver_id=driver.id, hub_id=hub.id,
                         event_type="off_shift", occurred_at=end)
    )
    await db_session.flush()


async def _route(db_session, hub, driver, plan):
    """`plan` is [(arrive_offset_min, depart_offset_min, [orders])], in sequence.

    Timed by geofence crossings unless `taps` is set, which is how the source
    precedence gets exercised.
    """
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)
    await db_session.flush()
    for index, entry in enumerate(plan, start=1):
        arrive_min, depart_min, orders = entry[0], entry[1], entry[2]
        timing = entry[3] if len(entry) > 3 else SOURCE_GEOFENCE
        stop = Stop(route_id=route.id, sequence=index, stop_type="dropoff", parcel_count=1)
        db_session.add(stop)
        await db_session.flush()
        arrive = DAY + timedelta(minutes=arrive_min)
        depart = DAY + timedelta(minutes=depart_min)
        if timing == SOURCE_GEOFENCE:
            for kind, when in ((KIND_ENTER, arrive), (KIND_EXIT, depart)):
                db_session.add(
                    StopGeofenceEvent(stop_id=stop.id, kind=kind, occurred_at=when,
                                      recorded_at=when, accuracy_m=12.0)
                )
        elif timing == SOURCE_TAPS:
            stop.arrived_at, stop.completed_at = arrive, depart
        for order in orders:
            db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    await db_session.flush()
    return route


async def _record_the_day(db_session, hub) -> dict:
    """Cost DAY's whole date through the ledger's one writer, the way
    `scripts/settle_month.py` does - a driver-day is the unit it writes in."""
    start = DAY.replace(hour=0)
    return await record_costs_for_period(
        db_session, hub_id=hub.id, since=start, until=start + timedelta(days=1)
    )


async def _day_cost(db_session, driver):
    """The driver's whole paid window, which is the unit a wage is paid in."""
    return await driver_day_cost(
        db_session,
        driver_id=driver.id,
        since=DAY - timedelta(hours=1),
        until=DAY + timedelta(hours=8),
    )


class TestTheSumIsTheWageBill:
    async def test_loaded_costs_add_up_to_what_the_driver_was_paid(self, db_session):
        """The property that makes these usable in aggregate. Own-time alone is
        smaller than the wage bill, so a cost-per-drop built from it would
        understate every comparison downstream and nothing would catch it."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=2))
        orders = [await _order(db_session, hub) for _ in range(3)]
        await _route(
            db_session, hub, driver,
            [(0, 10, [orders[0]]), (25, 30, [orders[1]]), (50, 60, [orders[2]])],
        )

        cost = await _day_cost(db_session, driver)
        assert cost is not None
        assert sum(o.loaded_cents for o in cost.orders) == pytest.approx(
            cost.total_cents, abs=3
        )

    async def test_own_time_alone_falls_short_of_it(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=2))
        orders = [await _order(db_session, hub) for _ in range(2)]
        await _route(
            db_session, hub, driver, [(0, 10, [orders[0]]), (40, 50, [orders[1]])]
        )
        cost = await _day_cost(db_session, driver)
        assert sum(o.own_cents for o in cost.orders) < cost.total_cents
        assert cost.overhead_seconds > 0

    async def test_a_stop_carrying_no_order_becomes_overhead_not_a_leak(self, db_session):
        """If its time were counted as attributed, the loaded costs would stop
        summing to the wage bill and the shortfall would be invisible."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=2))
        order = await _order(db_session, hub)
        await _route(
            db_session, hub, driver, [(0, 20, []), (30, 40, [order])]
        )
        cost = await _day_cost(db_session, driver)
        assert sum(o.loaded_cents for o in cost.orders) == pytest.approx(
            cost.total_cents, abs=3
        )
        assert any("carry no order" in note for note in cost.notes)


class TestAttribution:
    async def test_two_orders_at_one_dock_split_the_time(self, db_session):
        """Where batching shows up as money rather than as a stops-per-route
        statistic."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        shared = [await _order(db_session, hub) for _ in range(2)]
        alone = await _order(db_session, hub)
        await _route(
            db_session, hub, driver,
            [(0, 10, shared), (20, 30, [alone])],
        )
        cost = await _day_cost(db_session, driver)
        by_id = {o.order_id: o for o in cost.orders}
        assert by_id[shared[0].id].shared_with == 2
        assert by_id[alone.id].shared_with == 1
        # Same dwell at both docks, but one was split two ways.
        assert by_id[shared[0].id].stop_seconds == pytest.approx(
            by_id[alone.id].stop_seconds / 2
        )

    async def test_the_leg_in_is_charged_to_the_stop_it_reached(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        first, second = await _order(db_session, hub), await _order(db_session, hub)
        await _route(
            db_session, hub, driver, [(0, 5, [first]), (35, 40, [second])]
        )
        cost = await _day_cost(db_session, driver)
        by_id = {o.order_id: o for o in cost.orders}
        # 30 minutes between the first departure and the second arrival.
        assert by_id[second.id].travel_seconds == pytest.approx(1800)
        assert by_id[first.id].travel_seconds == 0.0

    async def test_overhead_is_shared_by_time_not_by_head(self, db_session):
        """A two-minute counter drop should not carry the same share of a long
        day as a forty-minute one."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=3))
        quick, slow = await _order(db_session, hub), await _order(db_session, hub)
        await _route(
            db_session, hub, driver, [(0, 2, [quick]), (10, 50, [slow])]
        )
        cost = await _day_cost(db_session, driver)
        by_id = {o.order_id: o for o in cost.orders}
        assert by_id[slow.id].overhead_seconds > by_id[quick.id].overhead_seconds


class TestWhereTheNumbersCameFrom:
    async def test_geofence_beats_taps(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        order = await _order(db_session, hub)
        await _route(db_session, hub, driver, [(0, 10, [order])])
        cost = await _day_cost(db_session, driver)
        assert cost.timing_source == SOURCE_GEOFENCE

    async def test_taps_are_used_when_there_are_no_crossings_and_it_is_flagged(
        self, db_session
    ):
        """Minute-level tap precision is the defect DRV-1 exists to replace, so
        a figure resting on it says so."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        order = await _order(db_session, hub)
        await _route(db_session, hub, driver, [(0, 10, [order], SOURCE_TAPS)])
        cost = await _day_cost(db_session, driver)
        assert cost.timing_source == "mixed"
        assert any("driver taps" in note for note in cost.notes)

    async def test_a_stop_timed_at_one_end_has_no_timing(self, db_session):
        """Half a timing is no timing. An arrival with no departure cannot be
        placed in a window, given a dwell, or start the next leg, so the stop is
        left out rather than handed on with a missing end."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub)
        route = await _route(db_session, hub, driver, [(0, 10, [order])])
        tapped_in_only = Stop(
            route_id=route.id, sequence=2, stop_type="dropoff", parcel_count=1,
            arrived_at=DAY + timedelta(minutes=20),
        )
        crossed_in_only = Stop(route_id=route.id, sequence=3, stop_type="dropoff", parcel_count=1)
        db_session.add_all([tapped_in_only, crossed_in_only])
        await db_session.flush()
        entered = DAY + timedelta(minutes=40)
        db_session.add(
            StopGeofenceEvent(stop_id=crossed_in_only.id, kind=KIND_ENTER, occurred_at=entered,
                              recorded_at=entered, accuracy_m=12.0)
        )
        await db_session.flush()

        stops = list(await db_session.scalars(select(Stop).where(Stop.route_id == route.id)))
        timings = await stop_timings(db_session, stops)

        assert [t.sequence for t in timings] == [1]
        assert timings[0].source == SOURCE_GEOFENCE

    async def test_a_completed_stop_with_no_timing_is_counted_and_said(self, db_session):
        """Completing a stop does not record an arrival, so a driver who skips
        "arrive" where the geofence missed a crossing leaves one. B's time lands
        in C's leg - which no figure shows - so the day says how many there were.
        A stop that never happened is not one of them."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(
            db_session, hub, driver, DAY - timedelta(minutes=30), DAY + timedelta(hours=2)
        )
        a, b, c = [await _order(db_session, hub) for _ in range(3)]
        route = await _route(db_session, hub, driver, [(0, 10, [a]), (40, 50, [c])])
        completed_untimed = Stop(
            route_id=route.id, sequence=3, stop_type="dropoff", parcel_count=1,
            completed_at=DAY + timedelta(minutes=30),
        )
        never_happened = Stop(route_id=route.id, sequence=4, stop_type="dropoff", parcel_count=1)
        db_session.add_all([completed_untimed, never_happened])
        await db_session.flush()
        db_session.add(StopOrder(stop_id=completed_untimed.id, order_id=b.id))
        await db_session.flush()

        cost = await _day_cost(db_session, driver)

        assert cost.untimed_stops == 1
        assert cost.summary()["untimed_stops"] == 1
        assert any("have no timing" in note for note in cost.notes)
        # Counted, not costed: no figure moves, and the day still sums to the wage.
        assert {o.order_id for o in cost.orders} == {a.id, c.id}
        assert sum(o.loaded_cents for o in cost.orders) == pytest.approx(
            cost.total_cents, abs=3
        )

    async def test_a_placeholder_wage_is_impossible_to_quote_unknowingly(self, db_session):
        """A cost computed from an invented wage is a fiction. Not refused - that
        would make this unusable on today's data - but it cannot be read without
        meeting the word."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub, rate=None)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        order = await _order(db_session, hub)
        await _route(db_session, hub, driver, [(0, 10, [order])])
        cost = await _day_cost(db_session, driver)
        assert cost.rate_source == RATE_PLACEHOLDER
        assert cost.rate_cents_per_hour == PLACEHOLDER_HOURLY_RATE_CENTS
        assert any("placeholder" in note for note in cost.notes)

    async def test_a_real_wage_is_recorded_as_such(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub, rate=RATE)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        order = await _order(db_session, hub)
        await _route(db_session, hub, driver, [(0, 10, [order])])
        cost = await _day_cost(db_session, driver)
        assert cost.rate_source == RATE_FROM_DRIVER
        assert not any("placeholder" in note for note in cost.notes)

    async def test_the_wage_covers_more_than_the_stops_and_the_gap_is_named(
        self, db_session
    ):
        """The reason a driver-day replaced a route as the unit. Reaching the
        first dock and returning from the last is real paid time; measuring
        first-arrival to last-departure made overhead exactly zero and quietly
        understated every drop."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY - timedelta(minutes=30),
                       DAY + timedelta(hours=2))
        order = await _order(db_session, hub)
        await _route(db_session, hub, driver, [(0, 10, [order])])
        cost = await _day_cost(db_session, driver)

        assert cost.overhead_seconds > 0
        assert cost.attributed_seconds < cost.paid_seconds
        assert any("overhead is the paid time" in note for note in cost.notes)

    async def test_no_shift_events_means_no_wage_to_attribute(self, db_session):
        """It does not fall back to the stop window. A day of drops with no
        shift behind it is a gap in the trace, and filling it from the stops
        would invent a wage nobody was paid."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub)
        await _route(db_session, hub, driver, [(0, 10, [order]), (20, 30, [order])])
        cost = await _day_cost(db_session, driver)
        assert cost.paid_seconds == 0
        assert cost.orders == []
        assert any("gap in the trace" in note for note in cost.notes)

    async def test_an_on_duty_day_with_no_timed_stop_attributes_nothing(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        order = await _order(db_session, hub)
        route = Route(hub_id=hub.id, driver_id=driver.id, status="planned")
        db_session.add(route)
        await db_session.flush()
        stop = Stop(route_id=route.id, sequence=1, stop_type="dropoff", parcel_count=1)
        db_session.add(stop)
        await db_session.flush()
        db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
        await db_session.flush()

        cost = await _day_cost(db_session, driver)
        assert cost.orders == []
        assert cost.paid_seconds > 0
        assert any("whole wage is unattributed" in note for note in cost.notes)

    async def test_a_route_that_does_not_exist_returns_nothing(self, db_session):
        cost = await driver_day_cost(
            db_session, driver_id=uuid.uuid4(),
            since=DAY, until=DAY + timedelta(hours=8),
        )
        assert cost.orders == []
        assert cost.rate_source == RATE_PLACEHOLDER


class TestItLandsInTheLedger:
    async def test_the_cost_is_an_outcome_not_a_field_on_the_order(self, db_session):
        """`a record the deciding code could read back and act on would stop
        being a record`. A cost on the order is a field dispatch can read."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        order = await _order(db_session, hub)
        await _route(db_session, hub, driver, [(0, 10, [order]), (20, 30, [order])])

        summary = await _record_the_day(db_session, hub)
        assert summary["costed"] == 1
        assert order.cost_actuals_cents is None

        recorded = await outcomes_for(db_session, subject_id=order.id)
        cost_entries = [e for e in recorded if e.kind == KIND_COST]
        assert len(cost_entries) == 1

    async def test_the_entry_carries_its_whole_basis(self, db_session):
        """An entry saying `1247` and nothing else would be unarguable with, and
        REC-3 exists so outcomes can be argued with."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        order = await _order(db_session, hub)
        await _route(db_session, hub, driver, [(0, 10, [order])])
        await _record_the_day(db_session, hub)

        entry = [
            e for e in await outcomes_for(db_session, subject_id=order.id)
            if e.kind == KIND_COST
        ][0]
        for key in (
            "loaded_cents", "own_cents", "rate_cents_per_hour", "rate_source",
            "timing_source", "shared_with", "notes",
        ):
            assert key in entry.values, key
        assert entry.values["rate_source"] == RATE_FROM_DRIVER

    async def test_recording_an_untimed_route_writes_nothing(self, db_session):
        """On duty, an order on the route, and no stop timed: there is a wage
        and nothing to divide it by, so nothing is written."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _on_duty(db_session, hub, driver, DAY, DAY + timedelta(hours=1))
        order = await _order(db_session, hub)
        route = Route(hub_id=hub.id, driver_id=driver.id, status="planned")
        db_session.add(route)
        await db_session.flush()
        stop = Stop(route_id=route.id, sequence=1, stop_type="dropoff", parcel_count=1)
        db_session.add(stop)
        await db_session.flush()
        db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
        await db_session.flush()

        summary = await _record_the_day(db_session, hub)

        assert summary["costed"] == 0
        recorded = await outcomes_for(db_session, subject_id=order.id)
        assert [e for e in recorded if e.kind == KIND_COST] == []
