"""
Device-bound driver auth: verify-otp upserts a DriverDevice row and binds
the issued token's device_id claim; a revoked device's token stops working
on its very next request (not just at refresh); refresh slides the
session forward without redoing OTP; re-verifying OTP un-revokes a device.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.driver_routes import (
    list_my_devices,
    refresh_token,
    request_otp,
    revoke_my_device,
    verify_otp,
)
from app.driver_auth.dependencies import get_current_driver
from app.driver_auth.tokens import decode_token
from app.models.driver import Driver
from app.models.driver_device import DriverDevice
from app.models.hub import Hub
from app.schemas.driver_auth import RequestOtpBody, VerifyOtpBody

pytestmark = pytest.mark.integration


async def _seed_driver(db_session):
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Device Auth Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Driver(id=driver_id, hub_id=hub_id, name="Sam D.", phone="+15555550300", vehicle_capacity_units=5))
    await db_session.commit()
    return hub_id, driver_id


async def _sign_in(db_session, phone: str, device_id: str) -> str:
    otp = await request_otp(RequestOtpBody(phone=phone), session=db_session)
    token = await verify_otp(
        VerifyOtpBody(phone=phone, code=otp.debug_code, device_id=device_id, device_name="Test Phone"),
        session=db_session,
    )
    return token.access_token


async def test_verify_otp_creates_a_driver_device_row(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    await _sign_in(db_session, "+15555550300", "device-a")

    result = await db_session.execute(select(DriverDevice).where(DriverDevice.driver_id == driver_id))
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.device_id == "device-a"
    assert row.device_name == "Test Phone"
    assert row.revoked_at is None


async def test_revoked_device_token_is_rejected_on_next_request(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    token = await _sign_in(db_session, "+15555550300", "device-a")

    authed = await get_current_driver(authorization=f"Bearer {token}")
    assert authed.driver_id == str(driver_id)

    await revoke_my_device("device-a", driver=authed, session=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_driver(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


async def test_refresh_issues_a_new_token_for_the_same_device(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    token = await _sign_in(db_session, "+15555550300", "device-a")
    authed = await get_current_driver(authorization=f"Bearer {token}")

    refreshed = await refresh_token(driver=authed, session=db_session)
    refreshed_driver_id, refreshed_hub_id, refreshed_device_id = decode_token(refreshed.access_token)
    assert refreshed_driver_id == str(driver_id)
    assert refreshed_device_id == "device-a"

    # The refreshed token itself must pass auth too.
    reauthed = await get_current_driver(authorization=f"Bearer {refreshed.access_token}")
    assert reauthed.driver_id == str(driver_id)


async def test_reverifying_otp_unrevokes_a_device(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    token = await _sign_in(db_session, "+15555550300", "device-a")
    authed = await get_current_driver(authorization=f"Bearer {token}")
    await revoke_my_device("device-a", driver=authed, session=db_session)

    with pytest.raises(HTTPException):
        await get_current_driver(authorization=f"Bearer {token}")

    # Signing in again with the same device_id (e.g. the driver got their
    # phone back) clears the revocation - a fresh OTP is itself re-proof
    # of identity.
    new_token = await _sign_in(db_session, "+15555550300", "device-a")
    reauthed = await get_current_driver(authorization=f"Bearer {new_token}")
    assert reauthed.driver_id == str(driver_id)


async def test_list_my_devices_marks_the_current_device(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    token_a = await _sign_in(db_session, "+15555550300", "device-a")
    await _sign_in(db_session, "+15555550300", "device-b")
    authed_a = await get_current_driver(authorization=f"Bearer {token_a}")

    devices = await list_my_devices(driver=authed_a, session=db_session)
    assert {d.device_id for d in devices} == {"device-a", "device-b"}
    current = next(d for d in devices if d.device_id == "device-a")
    other = next(d for d in devices if d.device_id == "device-b")
    assert current.is_current is True
    assert other.is_current is False
