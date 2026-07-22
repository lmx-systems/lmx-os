"""
OTP store (fakeredis, same pattern as tests/test_fleet_state_and_hold_queue.py)
and JWT token round-trip - the two pieces of app/driver_auth/ that don't
need a real Postgres driver row to exercise.
"""
from unittest.mock import patch

import pytest
from fakeredis import aioredis as fakeredis_aioredis

import app.driver_auth.otp_store as otp_store_module
from app.driver_auth.otp_store import MAX_ISSUE_ATTEMPTS, MAX_VERIFY_ATTEMPTS, OtpRateLimitExceeded, OtpStore
from app.driver_auth.tokens import (
    InvalidDriverToken,
    assert_driver_jwt_secret_configured,
    decode_token,
    issue_token,
)


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(otp_store_module, "get_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_otp_issue_then_verify_succeeds(fake_redis):
    store = OtpStore()
    issued = await store.issue("+15555550100")
    assert len(issued.code) == 4

    assert await store.verify("+15555550100", issued.code) is True


@pytest.mark.asyncio
async def test_otp_verify_is_single_use(fake_redis):
    store = OtpStore()
    issued = await store.issue("+15555550100")
    assert await store.verify("+15555550100", issued.code) is True
    # Replaying the same code a second time must fail - the key was deleted.
    assert await store.verify("+15555550100", issued.code) is False


@pytest.mark.asyncio
async def test_otp_verify_rejects_wrong_code(fake_redis):
    store = OtpStore()
    await store.issue("+15555550100")
    assert await store.verify("+15555550100", "0000") is False


@pytest.mark.asyncio
async def test_otp_verify_unknown_phone_fails_closed(fake_redis):
    store = OtpStore()
    assert await store.verify("+15555559999", "1234") is False


@pytest.mark.asyncio
async def test_otp_locks_out_after_max_failed_attempts(fake_redis):
    store = OtpStore()
    issued = await store.issue("+15555550100")
    wrong_code = "0000" if issued.code != "0000" else "1111"

    for _ in range(MAX_VERIFY_ATTEMPTS):
        assert await store.verify("+15555550100", wrong_code) is False

    # Even the real code no longer works - the key was invalidated after
    # the attempt cap was hit.
    assert await store.verify("+15555550100", issued.code) is False


@pytest.mark.asyncio
async def test_otp_issuance_is_rate_limited(fake_redis):
    store = OtpStore()
    for _ in range(MAX_ISSUE_ATTEMPTS):
        await store.issue("+15555550100")

    with pytest.raises(OtpRateLimitExceeded):
        await store.issue("+15555550100")


@pytest.mark.asyncio
async def test_otp_issuance_rate_limit_is_per_phone_number(fake_redis):
    store = OtpStore()
    for _ in range(MAX_ISSUE_ATTEMPTS):
        await store.issue("+15555550100")

    # A different phone number has its own independent budget.
    issued = await store.issue("+15555550200")
    assert len(issued.code) == 4


@pytest.mark.asyncio
async def test_check_rate_limit_can_be_charged_independently_of_issue(fake_redis):
    """app/api/driver_routes.py's request_otp charges this before checking
    whether the phone belongs to a real driver, so a phone-number-enumeration
    attempt against unregistered numbers still burns the same budget."""
    store = OtpStore()
    for _ in range(MAX_ISSUE_ATTEMPTS):
        await store.check_rate_limit("+15555550100")

    with pytest.raises(OtpRateLimitExceeded):
        await store.check_rate_limit("+15555550100")


@pytest.mark.asyncio
async def test_issue_with_skip_rate_limit_check_does_not_consume_the_budget(fake_redis):
    store = OtpStore()
    await store.check_rate_limit("+15555550100")  # 1/3 used

    # Simulates request_otp's real call shape: rate limit already charged
    # once above, then issue() must not charge it a second time.
    for _ in range(5):
        issued = await store.issue("+15555550100", skip_rate_limit_check=True)
        assert len(issued.code) == 4


@pytest.mark.asyncio
async def test_issue_sends_via_real_sms_client_when_twilio_is_configured(fake_redis, monkeypatch):
    """Regression test: issue() used to hardcode sent_via_sms=False in its
    returned result regardless of whether Twilio was actually configured,
    which meant app/api/driver_routes.py's request_otp always echoed the
    real OTP back in debug_code - even in production with real Twilio
    credentials, since no real send ever happened either. Both halves of
    that bug are covered here: the code must actually be sent, and the
    result must honestly report that it was."""
    monkeypatch.setattr(otp_store_module.settings, "twilio_account_sid", "AC-fake")
    monkeypatch.setattr(otp_store_module.settings, "twilio_auth_token", "fake-token")
    monkeypatch.setattr(otp_store_module.settings, "twilio_from_number", "+15555550001")

    sent = {}

    class FakeSmsClient:
        async def send(self, to, body):
            sent["to"] = to
            sent["body"] = body
            return "SM-fake"

    monkeypatch.setattr(otp_store_module, "get_sms_client", lambda: FakeSmsClient())

    store = OtpStore()
    issued = await store.issue("+15555550100")

    assert issued.sent_via_sms is True
    assert sent["to"] == "+15555550100"
    assert issued.code in sent["body"]


@pytest.mark.asyncio
async def test_issue_does_not_send_via_sms_when_twilio_is_unconfigured(fake_redis):
    store = OtpStore()
    issued = await store.issue("+15555550100")
    assert issued.sent_via_sms is False


def test_issue_and_decode_token_roundtrip():
    token = issue_token("driver-1", "hub-1", "device-1")
    driver_id, hub_id, device_id = decode_token(token)
    assert driver_id == "driver-1"
    assert hub_id == "hub-1"
    assert device_id == "device-1"


def test_decode_rejects_garbage_token():
    with pytest.raises(InvalidDriverToken):
        decode_token("not-a-real-token")


def test_refuses_to_start_with_default_secret_outside_development():
    with patch("app.driver_auth.tokens.settings") as mock_settings:
        mock_settings.driver_jwt_secret = "dev-only-insecure-secret-change-in-production"
        mock_settings.environment = "production"
        with pytest.raises(RuntimeError):
            assert_driver_jwt_secret_configured()


def test_allows_default_secret_in_development():
    with patch("app.driver_auth.tokens.settings") as mock_settings:
        mock_settings.driver_jwt_secret = "dev-only-insecure-secret-change-in-production"
        mock_settings.environment = "development"
        assert_driver_jwt_secret_configured()  # must not raise


def test_allows_a_real_secret_outside_development():
    with patch("app.driver_auth.tokens.settings") as mock_settings:
        mock_settings.driver_jwt_secret = "a-real-generated-secret"
        mock_settings.environment = "production"
        assert_driver_jwt_secret_configured()  # must not raise
