"""DRV-1: arrive and depart recorded to the second, with no driver tap.

`docs/ROADMAP_1.5.md` Phase 1. The roadmap calls DRV-1 and DRV-2 "the whole of
Phase 1's risk"; everything else in the phase is plumbing.

The claim under test is narrow and load-bearing: a machine-recorded boundary
crossing recovers the time a rounded clock and a forgotten tap threw away. The
design partner's export is minute-resolution and **65.5% of its stops compute to
zero dwell**; the one second-precision file shows those same stops really took
3-50 seconds. So the tests here care about seconds, about what happens when the
tap and the sensor disagree, and about the outbox replaying.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.api.driver_routes import record_geofence_events
from app.driver_auth.dependencies import AuthedDriver
from app.identity import refresh_dwell_statistics, resolve_location
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop
from app.models.stop_geofence_event import StopGeofenceEvent
from app.schemas.driver_app import StopGeofenceEventBody, StopGeofenceEventsBody

pytestmark = pytest.mark.integration

BASE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


async def _seed(db_session):
    hub = Hub(id=uuid.uuid4(), name="Geofence Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Geofence Client", pos_system="flat_file")
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

    address = "700 Sensor St, Austin, TX"
    location = await resolve_location(db_session, address=address)
    shop = Shop(client_id=client.id, name="Sensor Shop", address=address,
                lat=30.0, lng=-97.0, location_id=location.id)
    db_session.add(shop)
    await db_session.flush()
    return location, shop, route, AuthedDriver(
        driver_id=str(driver.id), hub_id=str(hub.id), device_id="test-device"
    )


async def _stop(db_session, route, shop, *, sequence, arrived=None, completed=None,
                status="completed"):
    stop = Stop(route_id=route.id, shop_id=shop.id, sequence=sequence, status=status,
                arrived_at=arrived, completed_at=completed)
    db_session.add(stop)
    await db_session.flush()
    return stop


async def _cross(db_session, stop, kind, occurred_at, *, accuracy_m=8.0):
    db_session.add(
        StopGeofenceEvent(
            stop_id=stop.id, kind=kind, occurred_at=occurred_at,
            recorded_at=datetime.now(timezone.utc), accuracy_m=accuracy_m,
        )
    )
    await db_session.flush()


async def test_dwell_comes_from_the_sensor_with_no_tap_at_all(db_session):
    """DRV-1's done-when: recorded to the second, no driver tap.

    Both tap columns are null here - the driver never pressed anything.
    """
    location, shop, route, _ = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)
    await _cross(db_session, stop, "enter", BASE)
    await _cross(db_session, stop, "exit", BASE + timedelta(seconds=47))

    profile = await refresh_dwell_statistics(db_session, location)

    assert stop.arrived_at is None and stop.completed_at is None
    assert profile.dwell_sample_count == 1
    assert profile.dwell_p50_seconds == 47, "to the second, not the minute"


async def test_the_sensor_beats_the_tap_when_they_disagree(db_session):
    """The whole argument for Phase 1, as one assertion.

    The tap says this stop took five minutes because the driver pressed
    'arrive' on the way in and 'complete' back at the van. The fence says
    forty seconds at the door.
    """
    location, shop, route, _ = await _seed(db_session)
    stop = await _stop(
        db_session, route, shop, sequence=0,
        arrived=BASE - timedelta(minutes=2),
        completed=BASE + timedelta(minutes=3),
    )
    await _cross(db_session, stop, "enter", BASE)
    await _cross(db_session, stop, "exit", BASE + timedelta(seconds=40))

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_p50_seconds == 40, "the tap-derived 300s must not win"

    # And the tap is still on the row, so the comparison stays available.
    assert stop.arrived_at is not None
    assert (stop.completed_at - stop.arrived_at).total_seconds() == 300


async def test_a_stop_with_no_crossings_still_uses_its_taps(db_session):
    """Permission denied, or the app was killed. A tap-derived dwell beats none."""
    location, shop, route, _ = await _seed(db_session)
    await _stop(
        db_session, route, shop, sequence=0,
        arrived=BASE, completed=BASE + timedelta(seconds=90),
    )

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 1
    assert profile.dwell_p50_seconds == 90


async def test_the_two_sources_mix_across_a_route(db_session):
    """Half the stops sensed, half tapped - one dock, one dwell figure."""
    location, shop, route, _ = await _seed(db_session)

    sensed = await _stop(db_session, route, shop, sequence=0)
    await _cross(db_session, sensed, "enter", BASE)
    await _cross(db_session, sensed, "exit", BASE + timedelta(seconds=30))

    await _stop(
        db_session, route, shop, sequence=1,
        arrived=BASE + timedelta(hours=1),
        completed=BASE + timedelta(hours=1, seconds=50),
    )

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 2
    assert profile.dwell_p50_seconds == 40


async def test_a_half_sensed_stop_falls_back_per_edge(db_session):
    """An entry recorded, the exit missed - the fence fired once and not again.

    Each edge falls back independently, so this is still one usable
    observation rather than a discarded stop.
    """
    location, shop, route, _ = await _seed(db_session)
    stop = await _stop(
        db_session, route, shop, sequence=0,
        arrived=BASE - timedelta(minutes=2),
        completed=BASE + timedelta(seconds=60),
    )
    await _cross(db_session, stop, "enter", BASE)

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 1
    assert profile.dwell_p50_seconds == 60, "sensed entry, tapped exit"


async def test_a_re_entry_takes_the_first_crossing_of_each_kind(db_session):
    """A driver who circles the block re-triggers the fence.

    First enter and first exit keeps the pair consistent; taking the latest
    enter against the earliest exit could produce a negative dwell.
    """
    location, shop, route, _ = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)
    await _cross(db_session, stop, "enter", BASE)
    await _cross(db_session, stop, "exit", BASE + timedelta(seconds=30))
    await _cross(db_session, stop, "enter", BASE + timedelta(seconds=90))
    await _cross(db_session, stop, "exit", BASE + timedelta(seconds=120))

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 1
    assert profile.dwell_p50_seconds == 30


async def test_a_replayed_crossing_cannot_be_recorded_twice(db_session):
    """The outbox retries. A replay must be a conflict, not a second arrival."""
    from sqlalchemy.exc import IntegrityError

    _, shop, route, _ = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)
    await _cross(db_session, stop, "enter", BASE)

    with pytest.raises(IntegrityError):
        await _cross(db_session, stop, "enter", BASE)
    await db_session.rollback()


async def test_crossings_survive_being_delivered_late(db_session):
    """DRV-4's outbox flushes a dead zone's worth at once.

    `occurred_at` is the device's clock, so a shift delivered on reconnect must
    still produce the dwell it actually had - not one collapsed onto the instant
    the signal came back.
    """
    location, shop, route, _ = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)

    # Both crossings happened this morning; both are being recorded now.
    await _cross(db_session, stop, "enter", BASE)
    await _cross(db_session, stop, "exit", BASE + timedelta(seconds=55))

    rows = list(
        await db_session.scalars(
            select(StopGeofenceEvent).where(StopGeofenceEvent.stop_id == stop.id)
        )
    )
    for row in rows:
        assert row.recorded_at > row.occurred_at, "delivery is later than the crossing"

    profile = await refresh_dwell_statistics(db_session, location)
    assert profile.dwell_p50_seconds == 55


async def test_a_failed_stop_is_still_censored_even_when_sensed(db_session):
    """M1b does not stop applying because the sensor is better.

    A stop the driver gave up on produced a lower bound, not a dwell, however
    precisely the boundary crossings were recorded.
    """
    location, shop, route, _ = await _seed(db_session)
    good = await _stop(db_session, route, shop, sequence=0)
    await _cross(db_session, good, "enter", BASE)
    await _cross(db_session, good, "exit", BASE + timedelta(seconds=40))

    failed = await _stop(db_session, route, shop, sequence=1, status="failed")
    await _cross(db_session, failed, "enter", BASE + timedelta(hours=1))
    await _cross(db_session, failed, "exit", BASE + timedelta(hours=1, seconds=900))

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 1, "the abandoned stop is not an observation"
    assert profile.dwell_p50_seconds == 40
    assert profile.dwell_censored_count == 1


async def test_a_crossing_pair_out_of_order_is_excluded_not_clamped(db_session):
    """An exit before its entry is a clock problem, not a zero-second stop."""
    location, shop, route, _ = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)
    await _cross(db_session, stop, "enter", BASE)
    await _cross(db_session, stop, "exit", BASE - timedelta(seconds=30))

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 0
    assert profile.dwell_p50_seconds is None


async def test_a_route_of_twenty_five_stops_is_measured_end_to_end(db_session):
    """The done-when says 25+ stops, which is the iOS 20-region cap plus slack.

    The cap is handled by rolling registration in the app; the backend has to
    cope with a route longer than the fence budget regardless, because that is
    what the design partner's best driver runs (20.2 stops).
    """
    location, shop, route, _ = await _seed(db_session)
    for index in range(25):
        stop = await _stop(db_session, route, shop, sequence=index)
        start = BASE + timedelta(minutes=20 * index)
        await _cross(db_session, stop, "enter", start)
        await _cross(db_session, stop, "exit", start + timedelta(seconds=30 + index))

    profile = await refresh_dwell_statistics(db_session, location)

    total = await db_session.scalar(
        select(func.count()).select_from(StopGeofenceEvent)
    )
    assert total == 50, "two crossings per stop"
    assert profile.dwell_sample_count == 25
    assert profile.dwell_p50_seconds == 42, "median of 30..54 seconds"
    assert profile.dwell_p90_seconds is not None, "25 samples clears the p90 floor"


# --- the endpoint (DRV-4's outbox is the caller) ---------------------------


def _body(*events):
    return StopGeofenceEventsBody(
        events=[
            StopGeofenceEventBody(kind=kind, occurred_at=at, accuracy_m=8.0)
            for kind, at in events
        ]
    )


async def test_the_endpoint_takes_a_whole_dead_zone_in_one_request(db_session):
    """A driver who loses signal at stop one and regains it at stop twelve.

    Batched because twenty-two round trips over a marginal connection is how
    you lose some of them.
    """
    _, shop, route, authed = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)

    result = await record_geofence_events(
        str(stop.id),
        _body(("enter", BASE), ("exit", BASE + timedelta(seconds=35))),
        driver=authed,
        session=db_session,
    )

    assert result.accepted == 2
    assert result.duplicates == 0
    assert result.rejected == 0


async def test_replaying_the_same_flush_writes_nothing_and_says_so(db_session):
    """The outbox cannot know its first attempt landed. Both answers mean
    "stop resending", but a replay must be visibly a no-op."""
    _, shop, route, authed = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)
    def flush():
        return _body(("enter", BASE), ("exit", BASE + timedelta(seconds=35)))

    first = await record_geofence_events(str(stop.id), flush(), driver=authed, session=db_session)
    second = await record_geofence_events(str(stop.id), flush(), driver=authed, session=db_session)

    assert (first.accepted, first.duplicates) == (2, 0)
    assert (second.accepted, second.duplicates) == (0, 2)

    total = await db_session.scalar(
        select(func.count()).select_from(StopGeofenceEvent).where(
            StopGeofenceEvent.stop_id == stop.id
        )
    )
    assert total == 2, "a replay doubled the crossings"


async def test_a_partly_new_flush_accepts_only_what_is_new(db_session):
    """The outbox flushed, half landed, the connection dropped, it retried."""
    _, shop, route, authed = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)

    await record_geofence_events(str(stop.id), _body(("enter", BASE)), driver=authed, session=db_session)
    result = await record_geofence_events(
        str(stop.id),
        _body(("enter", BASE), ("exit", BASE + timedelta(seconds=35))),
        driver=authed,
        session=db_session,
    )

    assert result.accepted == 1
    assert result.duplicates == 1


async def test_a_broken_device_clock_is_rejected_rather_than_stored(db_session):
    """And rejected in a way the outbox can act on.

    A phone whose clock says next week would otherwise jam its own queue
    forever behind an event that can never be accepted - so this is a counted
    rejection, not an error the client will retry.
    """
    _, shop, route, authed = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)

    result = await record_geofence_events(
        str(stop.id),
        _body(
            ("enter", datetime.now(timezone.utc) + timedelta(days=7)),
            ("exit", BASE + timedelta(seconds=35)),
        ),
        driver=authed,
        session=db_session,
    )

    assert result.rejected == 1
    assert result.accepted == 1, "the good crossing in the same flush still lands"


async def test_a_crossing_after_the_stop_finished_is_still_recorded(db_session):
    """A crossing is a fact about the physical world.

    Refusing a late exit because the state machine says the stop is done would
    discard evidence to protect a status column.
    """
    _, shop, route, authed = await _seed(db_session)
    stop = await _stop(
        db_session, route, shop, sequence=0,
        arrived=BASE, completed=BASE + timedelta(seconds=20), status="completed",
    )

    result = await record_geofence_events(
        str(stop.id),
        _body(("exit", BASE + timedelta(seconds=45))),
        driver=authed,
        session=db_session,
    )

    assert result.accepted == 1


async def test_another_drivers_stop_is_not_writable(db_session):
    """Ownership is checked the same way every other stop endpoint checks it."""
    from fastapi import HTTPException

    _, shop, route, _ = await _seed(db_session)
    stop = await _stop(db_session, route, shop, sequence=0)
    _, _, _, other_driver = await _seed(db_session)

    with pytest.raises(HTTPException):
        await record_geofence_events(
            str(stop.id), _body(("enter", BASE)), driver=other_driver, session=db_session
        )
