"""Signing a driver's phone in with a code from the ops console.

Replaces the texted code, so signing in needs no SMS provider. Ops issues the
code (at onboarding, or from the devices panel); the phone redeems it once for
a device-bound session. See app/driver_auth/sign_in_codes.py.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.api.admin_routes import admin_issue_sign_in_code, admin_list_sign_in_codes, onboard_driver
from app.api.driver_routes import sign_in
from app.driver_auth.dependencies import get_current_driver
from app.models.driver_sign_in_code import DriverSignInCode
from app.models.hub import Hub
from app.models.ops_user import OpsUser
from app.ops_auth.dependencies import AuthedOpsUser
from app.schemas.admin import DriverOnboardingBody
from app.schemas.driver_auth import SignInBody
from tests.integration.conftest import fake_request

pytestmark = pytest.mark.integration


async def _admin_and_hub(db_session):
    hub_id, ops_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Sign-in Hub", lat=30.27, lng=-97.74))
    db_session.add(
        OpsUser(id=ops_id, email=f"ops-{ops_id.hex[:6]}@example.com", password_hash="x", name="Dana K.", role="admin")
    )
    await db_session.commit()
    return hub_id, AuthedOpsUser(ops_user_id=str(ops_id), email="ops@example.com", name="Dana K.", role="admin")


async def _onboard(db_session, hub_id, admin):
    return await onboard_driver(
        DriverOnboardingBody(
            hub_id=str(hub_id), name="Lee M.", phone=f"+1555{uuid.uuid4().int % 10**7:07d}",
            vehicle_capacity_units=5, employment_type="w2",
        ),
        session=db_session,
        _admin=admin,
    )


async def _redeem(db_session, code: str, device_id: str = "phone-a"):
    return await sign_in(
        SignInBody(code=code, device_id=device_id, device_name="Phone"),
        request=fake_request(),
        session=db_session,
    )


async def test_onboarding_hands_back_a_code_that_signs_the_driver_in(db_session, real_redis_client):
    hub_id, admin = await _admin_and_hub(db_session)
    result = await _onboard(db_session, hub_id, admin)

    # Scanned as a QR code: the payload carries the code, never a server address.
    assert result.sign_in_code.qr_payload == f"LMX-SIGNIN:{result.sign_in_code.code}"
    token = await _redeem(db_session, result.sign_in_code.qr_payload)

    authed = await get_current_driver(authorization=f"Bearer {token.access_token}")
    assert authed.driver_id == result.driver_id and authed.device_id == "phone-a"


async def test_a_code_typed_with_its_dash_in_lower_case_works(db_session, real_redis_client):
    hub_id, admin = await _admin_and_hub(db_session)
    result = await _onboard(db_session, hub_id, admin)

    token = await _redeem(db_session, result.sign_in_code.display.lower())

    assert token.access_token


async def test_a_code_works_once(db_session, real_redis_client):
    hub_id, admin = await _admin_and_hub(db_session)
    result = await _onboard(db_session, hub_id, admin)
    await _redeem(db_session, result.sign_in_code.code, "phone-a")

    with pytest.raises(HTTPException) as exc:
        await _redeem(db_session, result.sign_in_code.code, "phone-b")

    assert exc.value.status_code == 401


async def test_a_new_code_retires_the_one_before_it(db_session, real_redis_client):
    hub_id, admin = await _admin_and_hub(db_session)
    result = await _onboard(db_session, hub_id, admin)

    newer = await admin_issue_sign_in_code(driver_id=result.driver_id, session=db_session, _admin=admin)

    with pytest.raises(HTTPException) as exc:
        await _redeem(db_session, result.sign_in_code.code)
    assert exc.value.status_code == 401
    assert (await _redeem(db_session, newer.code)).access_token


async def test_an_expired_code_is_refused(db_session, real_redis_client):
    hub_id, admin = await _admin_and_hub(db_session)
    result = await _onboard(db_session, hub_id, admin)
    await db_session.execute(
        update(DriverSignInCode).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await _redeem(db_session, result.sign_in_code.code)

    assert exc.value.status_code == 401


async def test_a_wrong_code_gets_the_same_answer_as_a_used_one(db_session, real_redis_client):
    """Unknown, used, replaced or expired all read the same, so a guess learns
    nothing about which."""
    hub_id, admin = await _admin_and_hub(db_session)
    result = await _onboard(db_session, hub_id, admin)
    await _redeem(db_session, result.sign_in_code.code)

    with pytest.raises(HTTPException) as used:
        await _redeem(db_session, result.sign_in_code.code)
    with pytest.raises(HTTPException) as wrong:
        await _redeem(db_session, "ZZZZZ-ZZZZZ")
    with pytest.raises(HTTPException) as garbage:
        await _redeem(db_session, "not a code")

    assert used.value.status_code == wrong.value.status_code == garbage.value.status_code == 401
    assert used.value.detail == wrong.value.detail == garbage.value.detail


async def test_only_a_hash_of_the_code_is_stored(db_session, real_redis_client):
    hub_id, admin = await _admin_and_hub(db_session)
    result = await _onboard(db_session, hub_id, admin)

    stored = (await db_session.scalars(select(DriverSignInCode.code_hmac))).all()

    assert len(stored) == 1 and result.sign_in_code.code not in stored[0]


async def test_the_history_says_who_let_which_phone_in(db_session, real_redis_client):
    hub_id, admin = await _admin_and_hub(db_session)
    result = await _onboard(db_session, hub_id, admin)
    await admin_issue_sign_in_code(driver_id=result.driver_id, session=db_session, _admin=admin)
    newest = await admin_issue_sign_in_code(driver_id=result.driver_id, session=db_session, _admin=admin)
    await _redeem(db_session, newest.code, "phone-z")

    history = await admin_list_sign_in_codes(driver_id=result.driver_id, session=db_session, _admin=admin)

    assert [h.status for h in history] == ["redeemed", "replaced", "replaced"]
    assert history[0].redeemed_device_id == "phone-z"
    assert all(h.issued_by == "Dana K." for h in history)
    assert not any(hasattr(h, "code") for h in history)
