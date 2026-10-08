"""
Integration coverage for the driver app's Phase 3: the support thread
(screens 1p/1q; the console's side is tests/integration/test_support_inbox.py)
and the placeholder earnings/trip-history estimate (screens 1n/1o). See
docs/NEXT_STEPS.md item 14.

Calls the route functions directly, same pattern as
tests/integration/test_driver_app_integration.py, whose _seed/
_accept_one_offer helpers this file reuses to get a real dropoff stop
with a real Order.delivery_contact_phone attached.
"""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import app.payroll.hours as payroll_hours
from app.api.driver_routes import (
    get_my_earnings,
    list_my_trips,
    list_support_messages,
    message_support,
)
from app.driver_auth.dependencies import AuthedDriver
from app.models.driver import Driver
from app.models.driver_shift_event import DriverShiftEvent
from app.models.hub import Hub
from app.models.route import Route
from app.models.stop import Stop
from app.schemas.driver_app import SendMessageBody

pytestmark = pytest.mark.integration



async def _seed_driver_only(db_session):
    """Lighter seed for the earnings/trip tests below, which don't need a
    full order/offer/route-acceptance chain - just a driver to attach
    hand-built Route rows to."""
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Earnings Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    # A distinct number per driver. `drivers.phone` is unique as of migration
    # 0063 - it is the login identity, and the OTP lookup raises on two rows -
    # so a shared one here collided as soon as a test seeded two drivers. The
    # number this file is actually about is the *counterparty* support line,
    # which is unaffected.
    db_session.add(
        Driver(
            id=driver_id, hub_id=hub_id, name="Sam E.",
            phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=5,
        )
    )
    await db_session.commit()
    return hub_id, driver_id


async def _seed_stop(db_session, hub_id, driver_id, status="arrived"):
    """A bare Stop for the reply-matching tests below - these only need a
    real stop_id to hang a Message/terminal-status check off, not a full
    order/offer/route-acceptance chain."""
    route = Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1)
    db_session.add(route)
    await db_session.flush()
    stop = Stop(route_id=route.id, sequence=0, status=status, stop_type="dropoff")
    db_session.add(stop)
    await db_session.commit()
    return stop.id


async def test_a_support_message_is_stored_in_the_drivers_thread(db_session):
    hub_id, driver_id = await _seed_driver_only(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    sent = await message_support(SendMessageBody(body="Gate code needed at 4th & Main"), driver=authed, session=db_session)
    assert sent.channel == "support"
    assert sent.stop_id is None

    thread = await list_support_messages(driver=authed, session=db_session)
    assert len(thread) == 1
    assert thread[0].body == "Gate code needed at 4th & Main"


async def test_support_messages_are_scoped_per_driver(db_session):
    hub_id, driver_id = await _seed_driver_only(db_session)
    _hub_id2, driver_id2 = await _seed_driver_only(db_session)
    authed_1 = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    authed_2 = AuthedDriver(driver_id=str(driver_id2), hub_id=str(hub_id), device_id="test-device-2")

    await message_support(SendMessageBody(body="From driver 1"), driver=authed_1, session=db_session)
    await message_support(SendMessageBody(body="From driver 2"), driver=authed_2, session=db_session)

    thread_1 = await list_support_messages(driver=authed_1, session=db_session)
    assert [m.body for m in thread_1] == ["From driver 1"]


async def test_earnings_computes_hours_from_shift_events_not_route_span(db_session):
    """Hours now come from the real online/offline log
    (app/models/driver_shift_event.py), not a completed route's
    created_at/updated_at span - a stale route sitting in the same window
    must not move the total at all."""
    hub_id, driver_id = await _seed_driver_only(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=now - timedelta(hours=3)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=now),
        ]
    )
    stale_route = Route(hub_id=hub_id, driver_id=driver_id, status="completed", plan_version=1)
    stale_route.created_at = now - timedelta(hours=6)
    stale_route.updated_at = now - timedelta(hours=5)
    db_session.add(stale_route)
    await db_session.commit()

    earnings = await get_my_earnings(driver=authed, session=db_session)
    assert earnings.is_placeholder is True
    assert earnings.hourly_rate_cents == payroll_hours.PLACEHOLDER_HOURLY_RATE_CENTS
    assert 2.9 <= earnings.hours_worked <= 3.1
    assert earnings.overtime_hours == 0.0
    assert earnings.estimated_pay_cents == round(earnings.hours_worked * payroll_hours.PLACEHOLDER_HOURLY_RATE_CENTS)


async def test_earnings_period_is_dated_on_the_hubs_clock(db_session):
    """A w2 driver's period is the hub's calendar month, from the 1st to the
    last day on the hub's clock - the dates a driver reads are the hours they
    cover. The hub keeps the model default, Los Angeles time."""
    hub_id, driver_id = await _seed_driver_only(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()

    earnings = await get_my_earnings(driver=authed, session=db_session)

    first = today.replace(day=1)
    following = first.replace(year=first.year + first.month // 12, month=first.month % 12 + 1)
    assert earnings.period_start == first
    assert earnings.period_end == following - timedelta(days=1)


async def test_earnings_excludes_shift_events_from_before_the_current_period(db_session):
    hub_id, driver_id = await _seed_driver_only(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    long_ago = datetime.now(timezone.utc) - timedelta(days=60)  # outside even a monthly (w2) window
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=long_ago),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=long_ago + timedelta(hours=2)),
        ]
    )
    await db_session.commit()

    earnings = await get_my_earnings(driver=authed, session=db_session)
    assert earnings.hours_worked == 0.0
    assert earnings.estimated_pay_cents == 0


async def test_earnings_is_zero_with_no_shift_events_even_with_an_active_route(db_session):
    hub_id, driver_id = await _seed_driver_only(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    active_route = Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1)
    db_session.add(active_route)
    await db_session.commit()

    earnings = await get_my_earnings(driver=authed, session=db_session)
    assert earnings.hours_worked == 0.0


async def test_earnings_uses_a_real_per_driver_rate_when_set(db_session):
    hub_id, driver_id = await _seed_driver_only(db_session)
    driver_row = await db_session.get(Driver, driver_id)
    driver_row.hourly_rate_cents = 2_500
    await db_session.commit()
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=now - timedelta(hours=2)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=now),
        ]
    )
    await db_session.commit()

    earnings = await get_my_earnings(driver=authed, session=db_session)
    assert earnings.is_placeholder is False
    assert earnings.hourly_rate_cents == 2_500


async def test_trips_lists_completed_routes_with_stop_counts_regardless_of_week(db_session):
    hub_id, driver_id = await _seed_driver_only(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    now = datetime.now(timezone.utc)
    route = Route(hub_id=hub_id, driver_id=driver_id, status="completed", plan_version=1)
    route.created_at = now - timedelta(days=20, hours=1)  # well outside this week
    route.updated_at = now - timedelta(days=20)
    db_session.add(route)
    await db_session.commit()
    db_session.add_all(
        [
            Stop(route_id=route.id, sequence=0, status="completed", stop_type="pickup"),
            Stop(route_id=route.id, sequence=1, status="completed", stop_type="dropoff"),
        ]
    )
    await db_session.commit()

    trips = await list_my_trips(driver=authed, session=db_session)
    assert len(trips) == 1
    assert trips[0].route_id == str(route.id)
    assert trips[0].stop_count == 2
    assert 0.9 <= trips[0].hours <= 1.1

    # Trip history isn't week-scoped like earnings - this old route still
    # doesn't show up in the current week's earnings estimate.
    earnings = await get_my_earnings(driver=authed, session=db_session)
    assert earnings.hours_worked == 0.0


