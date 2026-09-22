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
    register_push_token,
    request_otp,
    revoke_my_device,
    verify_otp,
)
from app.driver_auth.dependencies import get_current_driver
from app.driver_auth.tokens import decode_token
from app.models.driver import Driver
from app.models.driver_device import DriverDevice
from app.models.hub import Hub
from app.schemas.driver_auth import PushTokenBody, RequestOtpBody, VerifyOtpBody

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


async def test_register_push_token_stores_it_on_the_right_device(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    token = await _sign_in(db_session, "+15555550300", "device-a")
    authed = await get_current_driver(authorization=f"Bearer {token}")

    await register_push_token(
        PushTokenBody(device_id="device-a", expo_push_token="ExponentPushToken[abc123]"),
        driver=authed,
        session=db_session,
    )

    result = await db_session.execute(select(DriverDevice).where(DriverDevice.driver_id == driver_id))
    row = result.scalar_one()
    assert row.expo_push_token == "ExponentPushToken[abc123]"
    assert row.push_token_registered_at is not None


async def test_register_push_token_404s_for_a_device_that_never_signed_in(db_session, real_redis_client):
    hub_id, driver_id = await _seed_driver(db_session)
    token = await _sign_in(db_session, "+15555550300", "device-a")
    authed = await get_current_driver(authorization=f"Bearer {token}")

    with pytest.raises(HTTPException) as exc_info:
        await register_push_token(
            PushTokenBody(device_id="device-never-signed-in", expo_push_token="ExponentPushToken[xyz]"),
            driver=authed,
            session=db_session,
        )
    assert exc_info.value.status_code == 404


class TestAnAdminCanSeeWhatToRevoke:
    """The list that made the revocation usable (`docs/ROADMAP_AUDIT_2026-09.md`).

    `DELETE /admin/drivers/{id}/devices/{device_id}` describes itself as the
    *"driver calls dispatch, ops revokes on their behalf — lost phone, no app
    access"* path, and it takes a device id. The only list of device ids was
    `GET /driver/me/devices`, which is driver-authenticated — so ops had to
    already know an id they could only have got from the driver, who has lost
    the phone. That is the one case the route exists for.
    """

    async def _admin(self, db_session):
        from app.models.ops_user import OpsUser
        from app.ops_auth.dependencies import AuthedOpsUser

        ops_id = uuid.uuid4()
        db_session.add(
            OpsUser(
                id=ops_id,
                email=f"ops-{ops_id.hex[:6]}@lmxit.com",
                password_hash="x",
                name="Ops Admin",
                role="admin",
            )
        )
        await db_session.commit()
        return AuthedOpsUser(
            ops_user_id=str(ops_id), email="ops@lmxit.com", name="Ops Admin", role="admin"
        )

    async def test_it_lists_the_devices_a_driver_signed_in_on(
        self, db_session, real_redis_client
    ):
        from app.api.admin_routes import admin_list_driver_devices

        _, driver_id = await _seed_driver(db_session)
        await _sign_in(db_session, "+15555550300", "device-a")
        admin = await self._admin(db_session)

        devices = await admin_list_driver_devices(
            driver_id=str(driver_id), session=db_session, _admin=admin
        )

        assert [d.device_id for d in devices] == ["device-a"]
        assert devices[0].device_name == "Test Phone"
        assert devices[0].revoked_at is None

    async def test_a_revoked_device_stays_listed(self, db_session, real_redis_client):
        # "No device" and "a device somebody revoked on Tuesday" are different
        # answers to "why can this driver not sign in", and the driver-facing
        # list — which filters revoked rows out — cannot tell them apart.
        from app.api.admin_routes import (
            admin_list_driver_devices,
            admin_revoke_driver_device,
        )

        _, driver_id = await _seed_driver(db_session)
        await _sign_in(db_session, "+15555550300", "device-a")
        admin = await self._admin(db_session)

        await admin_revoke_driver_device(
            driver_id=str(driver_id),
            device_id="device-a",
            session=db_session,
            _admin=admin,
        )
        devices = await admin_list_driver_devices(
            driver_id=str(driver_id), session=db_session, _admin=admin
        )

        assert len(devices) == 1
        assert devices[0].revoked_at is not None

    async def test_the_phone_in_somebodys_hand_is_first(
        self, db_session, real_redis_client
    ):
        from app.api.admin_routes import admin_list_driver_devices

        _, driver_id = await _seed_driver(db_session)
        await _sign_in(db_session, "+15555550300", "old-phone")
        await _sign_in(db_session, "+15555550300", "new-phone")
        admin = await self._admin(db_session)

        devices = await admin_list_driver_devices(
            driver_id=str(driver_id), session=db_session, _admin=admin
        )

        assert devices[0].device_id == "new-phone"

    async def test_a_driver_who_never_signed_in_has_no_devices(
        self, db_session, real_redis_client
    ):
        from app.api.admin_routes import admin_list_driver_devices

        _, driver_id = await _seed_driver(db_session)
        admin = await self._admin(db_session)

        assert (
            await admin_list_driver_devices(
                driver_id=str(driver_id), session=db_session, _admin=admin
            )
            == []
        )
