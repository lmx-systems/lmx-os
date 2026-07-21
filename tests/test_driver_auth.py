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
