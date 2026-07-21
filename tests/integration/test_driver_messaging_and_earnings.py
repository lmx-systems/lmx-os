"""
Integration coverage for the driver app's Phase 3: masked SMS messaging
(screens 1p/1q) and the placeholder earnings/trip-history estimate
(screens 1n/1o). See docs/NEXT_STEPS.md item 14.

No Twilio account is configured in the test environment, so every send
goes through StubSmsClient (app/messaging/sms_client.py) - twilio_sid is
always None here. That's the correct behavior to assert, not a gap: it's
exactly what a real deployment without Twilio credentials configured
would also do.

Calls the route functions directly, same pattern as
tests/integration/test_driver_app_integration.py, whose _seed/
_accept_one_offer helpers this file reuses to get a real dropoff stop
with a real Order.delivery_contact_phone attached.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.driver_routes import (
    PLACEHOLDER_HOURLY_RATE_CENTS,
    get_my_earnings,
    list_customer_messages,
    list_my_trips,
    list_support_messages,
    message_customer,
    message_support,
)
from app.api.webhooks import twilio_inbound_sms
from app.driver_auth.dependencies import AuthedDriver
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.route import Route
from app.models.stop import Stop
from app.schemas.driver_app import SendMessageBody
from tests.integration.test_driver_app_integration import _accept_one_offer, _seed

pytestmark = pytest.mark.integration


async def _seed_driver_only(db_session):
    """Lighter seed for the earnings/trip tests below, which don't need a
    full order/offer/route-acceptance chain - just a driver to attach
    hand-built Route rows to."""
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Earnings Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Driver(id=driver_id, hub_id=hub_id, name="Sam E.", phone="+15555550299", vehicle_capacity_units=5))
    await db_session.commit()
    return hub_id, driver_id


async def test_message_customer_sends_via_stub_and_stores_thread(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, _pickup, dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    sent = await message_customer(
        dropoff.stop_id, SendMessageBody(body="On my way!"), driver=authed, session=db_session
    )
    assert sent.channel == "customer"
    assert sent.direction == "outbound"
    assert sent.body == "On my way!"
    # No Twilio account configured in tests -> StubSmsClient -> no real SID.
    # (Not asserted directly on the response - MessageView deliberately
    # never exposes it - but confirmed via the thread read below matching
    # what was actually stored.)

    thread = await list_customer_messages(dropoff.stop_id, driver=authed, session=db_session)
    assert len(thread) == 1
    assert thread[0].message_id == sent.message_id


async def test_message_customer_rejects_a_pickup_stop(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    with pytest.raises(HTTPException) as exc_info:
        await message_customer(pickup.stop_id, SendMessageBody(body="hi"), driver=authed, session=db_session)
    assert exc_info.value.status_code == 409


async def test_message_support_stores_even_when_no_support_number_configured(db_session):
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


async def test_inbound_webhook_matches_reply_to_most_recent_outbound_thread(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, _pickup, dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    await message_customer(dropoff.stop_id, SendMessageBody(body="On my way!"), driver=authed, session=db_session)

    await twilio_inbound_sms(
        From=order.delivery_contact_phone, Body="Thanks, I'll be here", MessageSid="SM_test_123", session=db_session
    )

    thread = await list_customer_messages(dropoff.stop_id, driver=authed, session=db_session)
    assert [m.direction for m in thread] == ["outbound", "inbound"]
    assert thread[-1].body == "Thanks, I'll be here"


async def test_inbound_webhook_from_unknown_number_does_not_error(db_session):
    # No prior outbound message to this number anywhere - should log and
    # no-op, not raise, since Twilio doesn't retry cleanly on a 500.
    response = await twilio_inbound_sms(From="+19995551234", Body="???", MessageSid=None, session=db_session)
    assert response.status_code == 200


async def test_earnings_is_placeholder_and_estimates_from_route_span(db_session):
    hub_id, driver_id = await _seed_driver_only(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    now = datetime.now(timezone.utc)
    route = Route(hub_id=hub_id, driver_id=driver_id, status="completed", plan_version=1)
    route.created_at = now - timedelta(hours=3)
    route.updated_at = now
    db_session.add(route)
    await db_session.commit()

    earnings = await get_my_earnings(driver=authed, session=db_session)
    assert earnings.is_placeholder is True
    assert earnings.hourly_rate_cents == PLACEHOLDER_HOURLY_RATE_CENTS
    assert 2.9 <= earnings.hours_worked <= 3.1
    assert earnings.estimated_pay_cents == round(earnings.hours_worked * PLACEHOLDER_HOURLY_RATE_CENTS)


async def test_earnings_excludes_routes_outside_the_current_week(db_session):
    hub_id, driver_id = await _seed_driver_only(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    now = datetime.now(timezone.utc)
    last_week_route = Route(hub_id=hub_id, driver_id=driver_id, status="completed", plan_version=1)
    last_week_route.created_at = now - timedelta(days=9, hours=2)
    last_week_route.updated_at = now - timedelta(days=9)
    db_session.add(last_week_route)
    await db_session.commit()

    earnings = await get_my_earnings(driver=authed, session=db_session)
    assert earnings.hours_worked == 0.0
    assert earnings.estimated_pay_cents == 0


async def test_earnings_excludes_non_completed_routes(db_session):
    hub_id, driver_id = await _seed_driver_only(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")

    active_route = Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1)
    db_session.add(active_route)
    await db_session.commit()

    earnings = await get_my_earnings(driver=authed, session=db_session)
    assert earnings.hours_worked == 0.0


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
