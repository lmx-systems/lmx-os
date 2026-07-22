"""
app/messaging/job_offer_notifications.py against real Postgres - which
devices actually get notified, and that a send failure never propagates.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.messaging.job_offer_notifications import notify_driver_of_new_offer
from app.models.driver import Driver
from app.models.driver_device import DriverDevice
from app.models.hub import Hub

pytestmark = pytest.mark.integration


async def _seed_driver(db_session) -> uuid.UUID:
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Push Notification Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Driver(id=driver_id, hub_id=hub_id, name="Sam D.", phone="+15555550301", vehicle_capacity_units=5))
    await db_session.commit()
    return driver_id


def _device(driver_id, device_id, *, token=None, revoked=False) -> DriverDevice:
    return DriverDevice(
        driver_id=driver_id,
        device_id=device_id,
        last_seen_at=datetime.now(timezone.utc),
        revoked_at=datetime.now(timezone.utc) if revoked else None,
        expo_push_token=token,
    )


async def test_sends_to_every_registered_non_revoked_device(db_session, real_redis_client):
    driver_id = await _seed_driver(db_session)
    db_session.add_all([
        _device(driver_id, "device-a", token="ExponentPushToken[a]"),
        _device(driver_id, "device-b", token="ExponentPushToken[b]"),
    ])
    await db_session.commit()

    fake_client = AsyncMock()
    with patch("app.messaging.job_offer_notifications.get_push_client", return_value=fake_client):
        await notify_driver_of_new_offer(str(driver_id), stop_count=2, ttl_seconds=120)

    sent_tokens = {call.args[0] for call in fake_client.send.await_args_list}
    assert sent_tokens == {"ExponentPushToken[a]", "ExponentPushToken[b]"}


async def test_skips_a_revoked_device(db_session, real_redis_client):
    driver_id = await _seed_driver(db_session)
    db_session.add_all([
        _device(driver_id, "device-a", token="ExponentPushToken[a]"),
        _device(driver_id, "device-revoked", token="ExponentPushToken[revoked]", revoked=True),
    ])
    await db_session.commit()

    fake_client = AsyncMock()
    with patch("app.messaging.job_offer_notifications.get_push_client", return_value=fake_client):
        await notify_driver_of_new_offer(str(driver_id), stop_count=1, ttl_seconds=120)

    sent_tokens = {call.args[0] for call in fake_client.send.await_args_list}
    assert sent_tokens == {"ExponentPushToken[a]"}


async def test_skips_a_device_with_no_registered_token(db_session, real_redis_client):
    driver_id = await _seed_driver(db_session)
    db_session.add(_device(driver_id, "device-a", token=None))
    await db_session.commit()

    fake_client = AsyncMock()
    with patch("app.messaging.job_offer_notifications.get_push_client", return_value=fake_client):
        await notify_driver_of_new_offer(str(driver_id), stop_count=1, ttl_seconds=120)

    fake_client.send.assert_not_awaited()


async def test_a_send_failure_never_raises(db_session, real_redis_client):
    driver_id = await _seed_driver(db_session)
    db_session.add(_device(driver_id, "device-a", token="ExponentPushToken[a]"))
    await db_session.commit()

    fake_client = AsyncMock()
    fake_client.send.side_effect = RuntimeError("network exploded")
    with patch("app.messaging.job_offer_notifications.get_push_client", return_value=fake_client):
        await notify_driver_of_new_offer(str(driver_id), stop_count=1, ttl_seconds=120)  # must not raise


async def test_notification_copy_mentions_stop_count_and_response_window(db_session, real_redis_client):
    driver_id = await _seed_driver(db_session)
    db_session.add(_device(driver_id, "device-a", token="ExponentPushToken[a]"))
    await db_session.commit()

    fake_client = AsyncMock()
    with patch("app.messaging.job_offer_notifications.get_push_client", return_value=fake_client):
        await notify_driver_of_new_offer(str(driver_id), stop_count=3, ttl_seconds=120)

    _, kwargs = fake_client.send.await_args
    args = fake_client.send.await_args.args
    title, body = args[1], args[2]
    assert "3 stops" in body
    assert "2 min" in body
    assert kwargs["data"] == {"type": "job_offer"}
