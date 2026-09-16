"""Is the geofence radius right? The measurement that answers it.

`GEOFENCE_RADIUS_M` is 75 because 75 seemed reasonable, not because anything
measured it. This is what replaces the guess, and it only works because DRV-1
kept both clocks: the driver's tap and the phone's crossing. Neither is ground
truth, but the distance between them is the quantity the radius controls.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop
from app.models.stop_geofence_event import StopGeofenceEvent
from app.reporting.geofence_calibration import (
    _percentile,
    measure_geofence_calibration,
)

pytestmark = pytest.mark.integration

BASE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


async def _seed(db_session):
    hub = Hub(id=uuid.uuid4(), name="Calibration Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Calibration Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    driver = Driver(
        id=uuid.uuid4(), hub_id=hub.id, name="Sam O.",
        phone=f"+1555555{uuid.uuid4().int % 10000:04d}", vehicle_capacity_units=10,
    )
    db_session.add(driver)
    await db_session.flush()
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)
    shop = Shop(client_id=client.id, name="Calibration Shop",
                address="900 Fence Rd, Austin, TX", lat=30.0, lng=-97.0)
    db_session.add(shop)
    await db_session.flush()
    return hub, shop, route


async def _stop(db_session, route, shop, *, sequence, lead_seconds=None,
                tapped=True, status="completed"):
    """One completed stop. `lead_seconds` is how early the fence fired."""
    arrived = BASE + timedelta(minutes=10 * sequence) if tapped else None
    stop = Stop(
        route_id=route.id, shop_id=shop.id, sequence=sequence, status=status,
        arrived_at=arrived,
        completed_at=(arrived or BASE) + timedelta(seconds=60),
    )
    db_session.add(stop)
    await db_session.flush()
    if lead_seconds is not None:
        db_session.add(
            StopGeofenceEvent(
                stop_id=stop.id, kind="enter",
                occurred_at=(arrived or BASE) - timedelta(seconds=lead_seconds),
                recorded_at=datetime.now(timezone.utc),
            )
        )
        await db_session.flush()
    return stop


async def test_lead_time_is_the_radius_expressed_in_time(db_session):
    """A fence firing a minute before the tap is firing a street away."""
    _, shop, route = await _seed(db_session)
    for index, lead in enumerate([10, 12, 14, 16, 18]):
        await _stop(db_session, route, shop, sequence=index, lead_seconds=lead)

    reading = await measure_geofence_calibration(db_session)

    assert reading.completed_stops == 5
    assert reading.stops_with_crossing == 5
    assert reading.comparable_stops == 5
    assert reading.lead_p50_seconds == pytest.approx(14.0)
    assert reading.lead_p90_seconds == pytest.approx(17.2)


async def test_coverage_shows_the_fence_missing_stops_entirely(db_session):
    """The other failure: a radius too small for the GPS the fleet really gets."""
    _, shop, route = await _seed(db_session)
    for index in range(4):
        await _stop(db_session, route, shop, sequence=index, lead_seconds=12)
    for index in range(4, 10):
        await _stop(db_session, route, shop, sequence=index, lead_seconds=None)

    reading = await measure_geofence_calibration(db_session)

    assert reading.completed_stops == 10
    assert reading.stops_with_crossing == 4
    assert reading.coverage == pytest.approx(0.4)
    assert any("40%" in note for note in reading.notes)
    assert any("permission" in note for note in reading.notes), (
        "a low coverage must not be read as a radius problem without that caveat"
    )


async def test_a_crossing_after_the_tap_is_counted_not_hidden(db_session):
    """A driver can tap arrive while still rolling up.

    Not an error, but a large share means the fence is tighter than the place
    drivers actually stop - the opposite diagnosis from a long lead, and it
    would be invisible if negative leads were dropped.
    """
    _, shop, route = await _seed(db_session)
    await _stop(db_session, route, shop, sequence=0, lead_seconds=20)
    await _stop(db_session, route, shop, sequence=1, lead_seconds=-15)

    reading = await measure_geofence_calibration(db_session)

    assert reading.comparable_stops == 2
    assert reading.late_crossings == 1


async def test_a_stop_with_a_crossing_but_no_tap_is_not_comparable(db_session):
    """It counts towards coverage - the sensor saw it - but there is nothing to
    measure the crossing against."""
    _, shop, route = await _seed(db_session)
    await _stop(db_session, route, shop, sequence=0, lead_seconds=12, tapped=False)

    reading = await measure_geofence_calibration(db_session)

    assert reading.stops_with_crossing == 1
    assert reading.comparable_stops == 0
    assert reading.lead_p50_seconds is None
    assert any("cannot be assessed" in note for note in reading.notes)


async def test_a_thin_sample_says_so_instead_of_reporting_a_median(db_session):
    """Four stops is one driver's habits, not the fleet's."""
    _, shop, route = await _seed(db_session)
    for index in range(4):
        await _stop(db_session, route, shop, sequence=index, lead_seconds=12)

    reading = await measure_geofence_calibration(db_session)

    assert reading.is_readable is False
    assert any("below the 30" in note for note in reading.notes)
    # The number is still computed - withholding it would be its own kind of
    # unhelpful - but it arrives with the caveat attached.
    assert reading.lead_p50_seconds is not None


async def test_thirty_comparable_stops_is_readable(db_session):
    _, shop, route = await _seed(db_session)
    for index in range(30):
        await _stop(db_session, route, shop, sequence=index, lead_seconds=12)

    reading = await measure_geofence_calibration(db_session)

    assert reading.is_readable is True
    assert reading.notes == []


async def test_failed_stops_are_excluded(db_session):
    """A failed stop has no dependable tap; including it would measure the
    exception path rather than the sensor."""
    _, shop, route = await _seed(db_session)
    await _stop(db_session, route, shop, sequence=0, lead_seconds=12)
    await _stop(db_session, route, shop, sequence=1, lead_seconds=12, status="failed")

    reading = await measure_geofence_calibration(db_session)

    assert reading.completed_stops == 1


async def test_the_window_bounds_what_is_measured(db_session):
    """A radius changed last week should be judged on this week's stops."""
    _, shop, route = await _seed(db_session)
    old = await _stop(db_session, route, shop, sequence=0, lead_seconds=90)
    old.completed_at = BASE - timedelta(days=30)
    await _stop(db_session, route, shop, sequence=1, lead_seconds=10)
    await db_session.flush()

    reading = await measure_geofence_calibration(
        db_session, since=BASE - timedelta(days=1)
    )

    assert reading.completed_stops == 1
    assert reading.lead_p50_seconds == pytest.approx(10.0)


async def test_the_hub_filter_goes_through_the_route(db_session):
    """Not through the shop: a client's orders can be carried by more than one
    hub, so filtering on the shop would mix them."""
    hub_a, shop, route_a = await _seed(db_session)
    await _stop(db_session, route_a, shop, sequence=0, lead_seconds=10)

    _, other_shop, route_b = await _seed(db_session)
    await _stop(db_session, route_b, other_shop, sequence=0, lead_seconds=90)

    reading = await measure_geofence_calibration(db_session, hub_id=hub_a.id)

    assert reading.completed_stops == 1
    assert reading.lead_p50_seconds == pytest.approx(10.0)


async def test_nothing_recorded_reads_as_nothing_rather_than_zero(db_session):
    reading = await measure_geofence_calibration(db_session)

    assert reading.completed_stops == 0
    assert reading.coverage == 0.0
    assert reading.lead_p50_seconds is None
    assert reading.is_readable is False


@pytest.mark.parametrize(
    "values,fraction,expected",
    [
        ([5.0], 0.5, 5.0),
        ([1.0, 2.0, 3.0], 0.5, 2.0),
        ([1.0, 2.0, 3.0, 4.0], 0.5, 2.5),
        ([10.0, 12.0, 14.0, 16.0, 18.0], 0.9, 17.2),  # verified against Postgres
    ],
)
def test_the_percentile_matches_postgres_percentile_cont(values, fraction, expected):
    """Same definition as the SQL one, so the two agree when somebody checks."""
    assert _percentile(values, fraction) == pytest.approx(expected)
