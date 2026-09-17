"""DEC-4: do we reach the door faster than the dispatcher did?

The baseline these are compared against is not invented - it is recomputed from
the design partner's export and reproduces the roadmap's figures exactly: 3,373
in-flight orders at a 34.0-minute median, 3,342 planned at 40.0, a 50.2% share.
`TestTheBaselineIsRecomputable` pins that, because a constant nobody can
reproduce is a constant that drifts.
"""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.reporting.insertion import (
    BASELINE_IN_FLIGHT_MEDIAN_MINUTES,
    BASELINE_IN_FLIGHT_SHARE,
    BASELINE_PLANNED_MEDIAN_MINUTES,
    in_flight_share,
    order_to_door_measurements,
)
from app.reporting.operations import build_operations_scorecard

pytestmark = pytest.mark.integration

PLANNED_AT = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 9, 17, tzinfo=timezone.utc)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Insertion Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _driver_route(db_session, hub, *, created_at=PLANNED_AT) -> Route:
    from app.models.driver import Driver

    driver = Driver(
        hub_id=hub.id, name="Driver", phone=f"+1512555{uuid.uuid4().hex[:4]}"
    )
    db_session.add(driver)
    await db_session.flush()
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    route.created_at = created_at
    db_session.add(route)
    await db_session.flush()
    return route


async def _delivered(db_session, hub, route, *, requested_at, door_minutes, sequence=1):
    order = Order(
        hub_id=hub.id, external_order_ref=f"PO-{uuid.uuid4().hex[:8]}",
        source_system="flat_file", raw_payload={}, sla_tier="T2",
        status=OrderStatus.delivered, requested_at=requested_at,
    )
    db_session.add(order)
    await db_session.flush()
    stop = Stop(
        route_id=route.id, sequence=sequence, stop_type="dropoff", parcel_count=1,
        arrived_at=requested_at + timedelta(minutes=door_minutes),
    )
    db_session.add(stop)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    await db_session.flush()
    return order


class TestTheSplit:
    async def test_an_order_requested_after_the_route_was_planned_is_in_flight(
        self, db_session
    ):
        """The live analogue of the export's rule - created after the route was
        dispatched. Same idea, different column names."""
        hub = await _hub(db_session)
        route = await _driver_route(db_session, hub, created_at=PLANNED_AT)
        await _delivered(
            db_session, hub, route,
            requested_at=PLANNED_AT + timedelta(minutes=20), door_minutes=30,
        )
        measurements = await order_to_door_measurements(db_session, SINCE)
        in_flight, planned = measurements
        assert in_flight.sample_size == 1
        assert planned.sample_size == 0

    async def test_an_order_that_predates_its_route_was_planned_for(self, db_session):
        hub = await _hub(db_session)
        route = await _driver_route(db_session, hub, created_at=PLANNED_AT)
        await _delivered(
            db_session, hub, route,
            requested_at=PLANNED_AT - timedelta(minutes=20), door_minutes=45,
        )
        in_flight, planned = await order_to_door_measurements(db_session, SINCE)
        assert in_flight.sample_size == 0
        assert planned.sample_size == 1

    async def test_the_two_halves_are_reported_apart(self, db_session):
        """A pooled median would average away the dispatcher's advantage, which
        shows up specifically on the orders they inserted by hand."""
        hub = await _hub(db_session)
        route = await _driver_route(db_session, hub)
        for index in range(3):
            await _delivered(
                db_session, hub, route,
                requested_at=PLANNED_AT + timedelta(minutes=10 + index),
                door_minutes=30, sequence=index + 1,
            )
        for index in range(3):
            await _delivered(
                db_session, hub, route,
                requested_at=PLANNED_AT - timedelta(minutes=10 + index),
                door_minutes=50, sequence=index + 10,
            )
        in_flight, planned = await order_to_door_measurements(db_session, SINCE)
        assert in_flight.median == pytest.approx(30 * 60)
        assert planned.median == pytest.approx(50 * 60)

    async def test_the_share_is_reported_with_its_denominator(self, db_session):
        hub = await _hub(db_session)
        route = await _driver_route(db_session, hub)
        await _delivered(
            db_session, hub, route,
            requested_at=PLANNED_AT + timedelta(minutes=5), door_minutes=30,
        )
        await _delivered(
            db_session, hub, route,
            requested_at=PLANNED_AT - timedelta(minutes=5), door_minutes=40,
            sequence=2,
        )
        rate = await in_flight_share(db_session, SINCE)
        assert (rate.numerator, rate.denominator) == (1, 2)
        assert "50.2%" in rate.target


class TestItRefusesRatherThanReportingZero:
    async def test_an_empty_window_says_why(self, db_session):
        """Before a pilot has run, the honest content is a reason - which is what
        the whole reporting vocabulary exists to preserve."""
        await _hub(db_session)
        in_flight, planned = await order_to_door_measurements(db_session, SINCE)
        assert in_flight.not_measured
        assert planned.not_measured
        assert in_flight.median is None
        rate = await in_flight_share(db_session, SINCE)
        assert rate.not_measured
        assert rate.percentage is None

    async def test_an_order_never_reached_is_not_counted_as_instant(self, db_session):
        hub = await _hub(db_session)
        route = await _driver_route(db_session, hub)
        order = Order(
            hub_id=hub.id, external_order_ref="PO-OPEN", source_system="flat_file",
            raw_payload={}, sla_tier="T2", status=OrderStatus.held,
            requested_at=PLANNED_AT + timedelta(minutes=5),
        )
        db_session.add(order)
        await db_session.flush()
        stop = Stop(route_id=route.id, sequence=1, stop_type="dropoff", parcel_count=1)
        db_session.add(stop)
        await db_session.flush()
        db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
        await db_session.flush()

        in_flight, planned = await order_to_door_measurements(db_session, SINCE)
        assert in_flight.sample_size == 0
        assert planned.sample_size == 0


class TestEveryFigureCarriesTheBaseline:
    async def test_the_target_names_where_the_number_came_from(self, db_session):
        await _hub(db_session)
        in_flight, planned = await order_to_door_measurements(db_session, SINCE)
        assert "34 min" in in_flight.target
        assert "40 min" in planned.target
        for measurement in (in_flight, planned):
            assert "design partner export" in measurement.target

    def test_the_constants_are_the_roadmap_figures(self):
        assert BASELINE_IN_FLIGHT_MEDIAN_MINUTES == 34.0
        assert BASELINE_PLANNED_MEDIAN_MINUTES == 40.0
        assert BASELINE_IN_FLIGHT_SHARE == 0.502


class TestItReachesTheEndpoint:
    async def test_the_scorecard_carries_it(self, db_session):
        """After the Phase 2 audit: a measurement nothing calls is a measurement
        nobody reads. This rides the scorecard `GET /operations/scorecard`
        already serves rather than waiting behind its own endpoint."""
        hub = await _hub(db_session)
        route = await _driver_route(db_session, hub)
        await _delivered(
            db_session, hub, route,
            requested_at=PLANNED_AT + timedelta(minutes=5), door_minutes=30,
        )
        scorecard = await build_operations_scorecard(
            db_session, window_days=30, now=PLANNED_AT + timedelta(days=1)
        )
        names = {m.name for m in scorecard.measurements}
        assert "Order to door, inserted after the route was planned" in names
        assert "Order to door, planned from the start" in names
        assert "Orders inserted after the route was planned" in {
            r.name for r in scorecard.rates
        }


class TestTheBaselineIsRecomputable:
    """A constant nobody can reproduce is a constant that drifts.

    Skipped wherever the export is absent, which is CI and any machine without
    the shared drive - `lmx-dwell/` is gitignored because this repository is
    public. Skipping is the point: the check runs where the data is, and its
    absence elsewhere is a fact about the checkout rather than a silent pass.
    """

    EXPORT = Path("lmx-dwell/out/stops_timing.csv")

    @pytest.mark.skipif(
        not EXPORT.exists(), reason="the design partner's export is not in this checkout"
    )
    def test_the_export_still_produces_the_roadmap_figures(self):
        import statistics

        from ml.real import load_timing

        stops, _ = load_timing(self.EXPORT)
        usable = [
            s for s in stops
            if s.order_to_door_sec is not None and s.hold_sec is not None
        ]
        in_flight = [
            s.order_to_door_sec / 60 for s in usable if s.was_inserted_in_flight
        ]
        planned = [
            s.order_to_door_sec / 60 for s in usable if not s.was_inserted_in_flight
        ]

        assert statistics.median(in_flight) == pytest.approx(
            BASELINE_IN_FLIGHT_MEDIAN_MINUTES, abs=0.5
        )
        assert statistics.median(planned) == pytest.approx(
            BASELINE_PLANNED_MEDIAN_MINUTES, abs=0.5
        )
        assert len(in_flight) / len(usable) == pytest.approx(
            BASELINE_IN_FLIGHT_SHARE, abs=0.005
        )
