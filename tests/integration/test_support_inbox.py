"""Drivers' support messages, read and answered in the ops console.

A driver writes from the app's support screen; it used to be texted to a support
number through Twilio, and nothing in the console showed it. Now dispatch reads
the thread here and replies, and the reply shows on the driver's screen.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.admin_routes import admin_reply_to_driver, admin_support_inbox, admin_support_thread
from app.api.driver_routes import list_support_messages, message_support
from app.driver_auth.dependencies import AuthedDriver
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.message import Message
from app.models.ops_user import OpsUser
from app.ops_auth.dependencies import AuthedOpsUser
from app.schemas.admin import SupportReplyBody
from app.schemas.driver_app import SendMessageBody

pytestmark = pytest.mark.integration


async def _hub_with_drivers(db_session, names: list[str]):
    hub_id, ops_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Support Hub", lat=30.27, lng=-97.74))
    db_session.add(
        OpsUser(id=ops_id, email=f"d-{ops_id.hex[:6]}@example.com", password_hash="x", name="Rosa D.", role="dispatcher")
    )
    await db_session.commit()
    drivers = []
    for name in names:
        driver = Driver(
            hub_id=hub_id, name=name, phone=f"+1555{uuid.uuid4().int % 10**7:07d}", vehicle_capacity_units=5
        )
        db_session.add(driver)
        drivers.append(driver)
    await db_session.commit()
    dispatcher = AuthedOpsUser(ops_user_id=str(ops_id), email="d@example.com", name="Rosa D.", role="dispatcher")
    return hub_id, drivers, dispatcher


def _as(driver: Driver) -> AuthedDriver:
    return AuthedDriver(driver_id=str(driver.id), hub_id=str(driver.hub_id), device_id="phone")


async def test_a_reply_reaches_the_drivers_thread(db_session, real_redis_client):
    hub_id, [driver], dispatcher = await _hub_with_drivers(db_session, ["Ana R."])
    await message_support(SendMessageBody(body="Gate is locked at 4th & Main"), driver=_as(driver), session=db_session)

    reply = await admin_reply_to_driver(
        str(driver.id), SupportReplyBody(body="Code is 2468"), session=db_session, ops=dispatcher
    )

    assert reply.from_driver is False and reply.sent_by == "Rosa D."
    thread = await list_support_messages(driver=_as(driver), session=db_session)
    assert [(m.direction, m.body) for m in thread] == [
        ("outbound", "Gate is locked at 4th & Main"),
        ("inbound", "Code is 2468"),
    ]


async def test_the_console_shows_the_whole_thread_and_who_answered(db_session, real_redis_client):
    hub_id, [driver], dispatcher = await _hub_with_drivers(db_session, ["Ana R."])
    await message_support(SendMessageBody(body="Customer not answering"), driver=_as(driver), session=db_session)
    await admin_reply_to_driver(str(driver.id), SupportReplyBody(body="Leave it at the counter"), session=db_session, ops=dispatcher)

    thread = await admin_support_thread(str(driver.id), session=db_session, _ops=dispatcher)

    assert [(m.from_driver, m.sent_by) for m in thread] == [(True, None), (False, "Rosa D.")]


async def test_threads_waiting_for_an_answer_come_first_oldest_first(db_session, real_redis_client):
    hub_id, [answered, waited_longest, just_wrote], dispatcher = await _hub_with_drivers(
        db_session, ["Answered A.", "Longest W.", "Recent R."]
    )
    now = datetime.now(timezone.utc)
    for driver, minutes_ago, direction in [
        (answered, 1, "inbound"),
        (waited_longest, 30, "outbound"),
        (just_wrote, 2, "outbound"),
    ]:
        db_session.add(
            Message(
                hub_id=hub_id, driver_id=driver.id, channel="support", direction=direction,
                body=f"from {driver.name}", created_at=now - timedelta(minutes=minutes_ago),
            )
        )
    await db_session.commit()

    inbox = await admin_support_inbox(str(hub_id), session=db_session, _ops=dispatcher)

    assert [(t.driver_name, t.awaiting_reply) for t in inbox] == [
        ("Longest W.", True),
        ("Recent R.", True),
        ("Answered A.", False),
    ]


async def test_nothing_is_texted_anywhere(db_session, real_redis_client):
    """The message is a row both apps read. No phone number is stored with it."""
    hub_id, [driver], dispatcher = await _hub_with_drivers(db_session, ["Ana R."])
    await message_support(SendMessageBody(body="Running late"), driver=_as(driver), session=db_session)

    row = (await db_session.execute(select(Message))).scalar_one()

    assert not hasattr(row, "counterparty_phone") and not hasattr(row, "twilio_sid")
