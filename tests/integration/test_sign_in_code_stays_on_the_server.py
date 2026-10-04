"""A driver's sign-in code leaves the server by text message, and no other way.

request_otp used to return the code in its response (debug_code) whenever
Twilio wasn't configured, in every environment. infra/aws starts with empty
TWILIO_* secrets, so the first deploy would have let anyone who knew a
registered driver's phone number sign in as them: ask for a code, read it out
of the response, send it back. The code now comes back only in local
development, which has no SMS provider by design. Anywhere else, sign-in
refuses until Twilio is configured.

Tests run with ENVIRONMENT=test, which is strict like production.
"""
import uuid

import pytest
from fastapi import HTTPException

import app.driver_auth.otp_store as otp_store_module
from app.api.driver_routes import request_otp, verify_otp
from app.config import settings
from app.models.driver import Driver
from app.models.hub import Hub
from app.schemas.driver_auth import RequestOtpBody, VerifyOtpBody

pytestmark = pytest.mark.integration

PHONE = "+15555550410"


async def _seed_driver(db_session) -> None:
    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Sign-in Code Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(
        Driver(id=uuid.uuid4(), hub_id=hub_id, name="Pat S.", phone=PHONE, vehicle_capacity_units=5)
    )
    await db_session.commit()


async def test_without_sms_a_deployed_server_refuses_instead_of_returning_the_code(
    db_session, real_redis_client
):
    await _seed_driver(db_session)
    assert settings.environment != "development"

    with pytest.raises(HTTPException) as exc_info:
        await request_otp(RequestOtpBody(phone=PHONE), session=db_session)

    assert exc_info.value.status_code == 503
    # Nothing was issued, so there is no code to guess or replay either.
    assert not await real_redis_client.exists(otp_store_module._key(PHONE))


async def test_in_local_development_the_code_comes_back_so_the_app_runs_without_a_phone(
    db_session, real_redis_client, monkeypatch
):
    await _seed_driver(db_session)
    monkeypatch.setattr(settings, "environment", "development")

    issued = await request_otp(RequestOtpBody(phone=PHONE), session=db_session)

    assert issued.debug_code is not None
    verified = VerifyOtpBody(phone=PHONE, code=issued.debug_code, device_id="dev-phone")
    assert (await verify_otp(verified, session=db_session)).access_token


@pytest.mark.parametrize("environment", ["development", "production"])
async def test_with_sms_the_code_is_texted_and_never_returned(
    db_session, real_redis_client, monkeypatch, environment
):
    await _seed_driver(db_session)
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "twilio_account_sid", "AC-fake")
    monkeypatch.setattr(settings, "twilio_auth_token", "fake-token")
    monkeypatch.setattr(settings, "twilio_from_number", "+15555550001")
    texts = []

    class FakeSmsClient:
        async def send(self, to, body):
            texts.append((to, body))
            return "SM-fake"

    monkeypatch.setattr(otp_store_module, "get_sms_client", lambda: FakeSmsClient())

    issued = await request_otp(RequestOtpBody(phone=PHONE), session=db_session)

    assert issued.debug_code is None
    [(to, body)] = texts
    assert to == PHONE
    texted_code = body.rsplit(" ", 1)[-1]
    verified = VerifyOtpBody(phone=PHONE, code=texted_code, device_id="real-phone")
    assert (await verify_otp(verified, session=db_session)).access_token
