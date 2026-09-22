"""DRV-3: turnaround measured per return trip.

`DRV-1` measures how long a driver spends at a customer's dock. This measures
how long they spend at ours — the time between coming back off a route and
leaving on the next one. It is a real cost input rather than a curiosity: `M4`
counts the driver-hours a route consumes, and the twenty minutes spent reloading
in the yard are as real as the twenty spent driving. Until now only the driving
was visible, so every trip cost understated itself by whatever the turnaround
was.

The tests worth reading are in `TestWhatIsNotATurnaround`. Pairing crossings
naively is easy and wrong in three separate directions, and each one inflates
the number rather than deflating it — which is the direction that makes a cost
model look better than it is.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.delivery.turnaround import MAX_PLAUSIBLE_TURNAROUND, turnarounds_for_hub
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.hub_geofence_event import KIND_ENTER, KIND_EXIT, HubGeofenceEvent

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Turnaround Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _driver(db_session, hub) -> Driver:
    driver = Driver(
        hub_id=hub.id, name="Sam O.",
        phone=f"+1555555{uuid.uuid4().int % 10000:04d}", vehicle_capacity_units=5,
    )
    db_session.add(driver)
    await db_session.flush()
    return driver


async def _crossing(db_session, hub, driver, kind, at, accuracy=12.0):
    db_session.add(
        HubGeofenceEvent(
            hub_id=hub.id, driver_id=driver.id, kind=kind,
            occurred_at=at, recorded_at=at, accuracy_m=accuracy,
        )
    )
    await db_session.flush()


class TestOneReturnTrip:
    async def test_an_enter_and_the_next_exit_is_a_turnaround(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _crossing(db_session, hub, driver, KIND_ENTER, NOW)
        await _crossing(db_session, hub, driver, KIND_EXIT, NOW + timedelta(minutes=18))

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert len(report.turnarounds) == 1
        assert report.turnarounds[0].seconds == 18 * 60
        assert report.median_seconds == 18 * 60

    async def test_several_trips_by_one_driver_all_count(self, db_session):
        """A driver does three runs in a day. Each return is its own
        turnaround, not one long visit."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        for hour, minutes in ((0, 10), (3, 20), (6, 30)):
            start = NOW + timedelta(hours=hour)
            await _crossing(db_session, hub, driver, KIND_ENTER, start)
            await _crossing(
                db_session, hub, driver, KIND_EXIT, start + timedelta(minutes=minutes)
            )

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert len(report.turnarounds) == 3
        assert report.median_seconds == 20 * 60

    async def test_two_drivers_do_not_pair_with_each_other(self, db_session):
        """Several drivers cross the same fence within a minute. Pairing one
        driver's arrival with another's departure would invent a turnaround
        neither of them had."""
        hub = await _hub(db_session)
        a = await _driver(db_session, hub)
        b = await _driver(db_session, hub)
        await _crossing(db_session, hub, a, KIND_ENTER, NOW)
        await _crossing(db_session, hub, b, KIND_ENTER, NOW + timedelta(seconds=30))
        await _crossing(db_session, hub, b, KIND_EXIT, NOW + timedelta(minutes=5))
        await _crossing(db_session, hub, a, KIND_EXIT, NOW + timedelta(minutes=40))

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        by_driver = {t.driver_id: t.seconds for t in report.turnarounds}
        assert by_driver[a.id] == 40 * 60
        assert by_driver[b.id] == pytest.approx(4.5 * 60)

    async def test_another_hubs_crossings_are_not_counted(self, db_session):
        hub = await _hub(db_session)
        elsewhere = await _hub(db_session)
        driver = await _driver(db_session, elsewhere)
        await _crossing(db_session, elsewhere, driver, KIND_ENTER, NOW)
        await _crossing(db_session, elsewhere, driver, KIND_EXIT, NOW + timedelta(minutes=5))

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert report.turnarounds == []


class TestWhatIsNotATurnaround:
    """Three ways the naive pairing inflates the number."""

    async def test_an_arrival_with_no_departure_is_open_not_zero(self, db_session):
        """They are still in the yard, or the shift ended there. Recording zero
        would pull the median down; recording it as very long would push it
        up. It is neither — it is unfinished."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _crossing(db_session, hub, driver, KIND_ENTER, NOW)

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert report.turnarounds == []
        assert report.open_visits == 1
        assert report.median_seconds is None

    async def test_a_departure_with_no_arrival_is_not_paired_backwards(self, db_session):
        """The first departure of the day, or a crossing lost while permission
        was off (DRV-5). Pairing it with the previous arrival would report an
        overnight as a turnaround."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _crossing(db_session, hub, driver, KIND_EXIT, NOW)

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert report.turnarounds == []
        assert report.unpaired_exits == 1

    async def test_an_overnight_is_implausible_rather_than_a_long_turnaround(
        self, db_session
    ):
        """A fence records a crossing, not an intention. An enter at 6pm paired
        with an exit at 7am is a car park."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _crossing(db_session, hub, driver, KIND_ENTER, NOW)
        await _crossing(
            db_session, hub, driver, KIND_EXIT,
            NOW + MAX_PLAUSIBLE_TURNAROUND + timedelta(minutes=1),
        )

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert report.turnarounds == []
        assert report.implausible == 1

    async def test_two_arrivals_running_do_not_span_the_gap(self, db_session):
        """An exit was lost. The gap between two arrivals is time on the road,
        not time in the yard, and pairing across it would report the whole run
        as turnaround."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _crossing(db_session, hub, driver, KIND_ENTER, NOW)
        await _crossing(db_session, hub, driver, KIND_ENTER, NOW + timedelta(hours=2))
        await _crossing(
            db_session, hub, driver, KIND_EXIT, NOW + timedelta(hours=2, minutes=15)
        )

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert len(report.turnarounds) == 1
        assert report.turnarounds[0].seconds == 15 * 60
        assert report.open_visits == 1


class TestKnowingWhetherToTrustIt:
    async def test_the_pairing_rate_says_how_much_was_usable(self, db_session):
        """A hub where half the crossings never pair has a sensor problem - a
        fence too tight, or permission switched off mid-shift - and a median
        from the half that did pair would look perfectly healthy."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _crossing(db_session, hub, driver, KIND_ENTER, NOW)
        await _crossing(db_session, hub, driver, KIND_EXIT, NOW + timedelta(minutes=10))
        await _crossing(db_session, hub, driver, KIND_ENTER, NOW + timedelta(hours=1))

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert report.pairing_rate == 0.5

    async def test_no_arrivals_at_all_is_not_a_rate_of_zero(self, db_session):
        """0% and "nothing happened" are different, and only one of them is a
        fence problem."""
        hub = await _hub(db_session)

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert report.pairing_rate is None

    async def test_the_window_is_respected(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _crossing(db_session, hub, driver, KIND_ENTER, NOW - timedelta(days=10))
        await _crossing(
            db_session, hub, driver, KIND_EXIT, NOW - timedelta(days=10) + timedelta(minutes=5)
        )

        report = await turnarounds_for_hub(
            db_session, hub_id=hub.id, since=NOW - timedelta(days=1)
        )

        assert report.turnarounds == []

    async def test_pairing_follows_the_phones_clock_not_ours(self, db_session):
        """A dead zone flushes a whole afternoon at one instant. Ordering by
        `recorded_at` would interleave a driver's morning with their afternoon
        the moment the outbox caught up."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        flushed_at = NOW + timedelta(hours=5)
        db_session.add_all([
            HubGeofenceEvent(
                hub_id=hub.id, driver_id=driver.id, kind=KIND_EXIT,
                occurred_at=NOW + timedelta(minutes=12), recorded_at=flushed_at,
            ),
            HubGeofenceEvent(
                hub_id=hub.id, driver_id=driver.id, kind=KIND_ENTER,
                occurred_at=NOW, recorded_at=flushed_at,
            ),
        ])
        await db_session.flush()

        report = await turnarounds_for_hub(db_session, hub_id=hub.id)

        assert len(report.turnarounds) == 1
        assert report.turnarounds[0].seconds == 12 * 60


class TestTheEndpoint:
    async def test_a_crossing_is_recorded_against_the_drivers_own_hub(self, db_session):
        """The hub comes from the token. A phone that could name its hub could
        name somebody else's."""
        from sqlalchemy import select

        from app.api.driver_routes import record_hub_geofence_events
        from app.driver_auth.dependencies import AuthedDriver
        from app.schemas.driver_app import HubGeofenceEventsBody, StopGeofenceEventBody

        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await db_session.commit()

        result = await record_hub_geofence_events(
            body=HubGeofenceEventsBody(
                events=[StopGeofenceEventBody(kind="enter", occurred_at=NOW, accuracy_m=9.0)]
            ),
            driver=AuthedDriver(
                driver_id=str(driver.id), hub_id=str(hub.id), device_id="d1"
            ),
            session=db_session,
        )

        assert result.accepted == 1
        event = await db_session.scalar(select(HubGeofenceEvent))
        assert event.hub_id == hub.id
        assert event.driver_id == driver.id

    async def test_a_replayed_crossing_is_a_duplicate_not_a_second_arrival(
        self, db_session
    ):
        """The outbox retries. A replay that wrote a second row would invent a
        turnaround out of one visit."""
        from app.api.driver_routes import record_hub_geofence_events
        from app.driver_auth.dependencies import AuthedDriver
        from app.schemas.driver_app import HubGeofenceEventsBody, StopGeofenceEventBody

        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await db_session.commit()
        authed = AuthedDriver(
            driver_id=str(driver.id), hub_id=str(hub.id), device_id="d1"
        )
        body = HubGeofenceEventsBody(
            events=[StopGeofenceEventBody(kind="enter", occurred_at=NOW)]
        )

        first = await record_hub_geofence_events(
            body=body, driver=authed, session=db_session
        )
        second = await record_hub_geofence_events(
            body=body, driver=authed, session=db_session
        )

        assert (first.accepted, first.duplicates) == (1, 0)
        assert (second.accepted, second.duplicates) == (0, 1)

    async def test_a_crossing_from_the_future_is_rejected_not_stored(self, db_session):
        """A phone with a broken clock. Accepting it would put a turnaround in
        next week; retrying it for ever would jam its own outbox."""
        from app.api.driver_routes import record_hub_geofence_events
        from app.driver_auth.dependencies import AuthedDriver
        from app.schemas.driver_app import HubGeofenceEventsBody, StopGeofenceEventBody

        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await db_session.commit()

        result = await record_hub_geofence_events(
            body=HubGeofenceEventsBody(
                events=[
                    StopGeofenceEventBody(
                        kind="enter",
                        occurred_at=datetime.now(timezone.utc) + timedelta(days=2),
                    )
                ]
            ),
            driver=AuthedDriver(
                driver_id=str(driver.id), hub_id=str(hub.id), device_id="d1"
            ),
            session=db_session,
        )

        assert result.rejected == 1
        assert result.accepted == 0

    async def test_the_profile_carries_the_hub_position(self, db_session):
        """So the app can put a fence round it. On the profile rather than a
        route: the fence should be live whenever a driver is on duty, and a
        driver back in the yard with no next route is exactly the turnaround
        worth measuring."""
        from app.api.driver_routes import get_my_profile
        from app.driver_auth.dependencies import AuthedDriver

        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await db_session.commit()

        view = await get_my_profile(
            driver=AuthedDriver(
                driver_id=str(driver.id), hub_id=str(hub.id), device_id="d1"
            ),
            session=db_session,
        )

        assert view.hub_lat == pytest.approx(30.27)
        assert view.hub_lng == pytest.approx(-97.74)


class TestItIsReadBack:
    """The orphan check caught this one on me.

    `turnarounds_for_hub` was written, tested, and called by nothing — the
    exact defect `docs/ROADMAP_AUDIT_2026-09.md` is about, committed while
    fixing that class of defect. `tests/test_no_new_orphans.py` failed on the
    first full run and named it.
    """

    async def test_record_health_reports_the_median_and_the_pairing_rate(
        self, db_session
    ):
        from app.reporting.record_health import build_record_health

        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        recent = datetime.now(timezone.utc) - timedelta(hours=2)
        await _crossing(db_session, hub, driver, KIND_ENTER, recent)
        await _crossing(db_session, hub, driver, KIND_EXIT, recent + timedelta(minutes=20))
        await db_session.commit()

        health = await build_record_health(db_session, hub_id=hub.id)

        assert health.turnaround_median_seconds == 20 * 60
        assert health.turnaround_trips == 1
        assert health.turnaround_pairing_rate == 1.0

    async def test_it_refuses_rather_than_reporting_zero_with_no_crossings(
        self, db_session
    ):
        """A hub with no fence data has no turnaround, which is different from a
        turnaround of nothing."""
        from app.reporting.record_health import build_record_health

        hub = await _hub(db_session)
        await db_session.commit()

        health = await build_record_health(db_session, hub_id=hub.id)

        assert health.turnaround_median_seconds is None
        assert health.turnaround_pairing_rate is None
        assert health.turnaround_trips == 0
