"""
Client portal auth (Phase 8) - the two pieces of app/client_auth/ that
don't need a real Postgres Client row to exercise: password hashing and
JWT token round-trip. Mirrors tests/test_driver_auth.py's token/secret
coverage for the equivalent driver-app pieces.
"""
from unittest.mock import patch

import pytest
from fakeredis import aioredis as fakeredis_aioredis

import app.client_auth.login_rate_limit as login_rate_limit_module
from app.client_auth.login_rate_limit import MAX_LOGIN_ATTEMPTS, LoginRateLimitExceeded, LoginRateLimiter
from app.client_auth.passwords import hash_password, verify_password
from app.client_auth.tokens import (
    InvalidClientToken,
    assert_client_jwt_secret_configured,
    decode_token,
    issue_token,
)


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(login_rate_limit_module, "get_client", lambda: client)
    return client


def test_hash_and_verify_password_roundtrip():
    password_hash = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", password_hash) is True


def test_verify_password_rejects_wrong_password():
    password_hash = hash_password("correct horse battery staple")
    assert verify_password("wrong password", password_hash) is False


def test_hash_password_never_stores_plaintext():
    plain = "correct horse battery staple"
    assert hash_password(plain) != plain


def test_verify_password_fails_closed_on_malformed_hash():
    # Never crash a login attempt over a malformed/legacy hash - just
    # treat it as a non-match (see app/client_auth/passwords.py).
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_issue_and_decode_token_roundtrip():
    token = issue_token("client-1")
    assert decode_token(token) == "client-1"


def test_decode_rejects_garbage_token():
    with pytest.raises(InvalidClientToken):
        decode_token("not-a-real-token")


def test_client_and_driver_tokens_are_not_interchangeable_with_distinct_secrets():
    # A client token must not decode successfully against a *different*
    # driver secret - see app/config.py's client_jwt_secret docstring for
    # why these are meant to be deliberately separate secrets in any real
    # deployment. (In this repo's shared dev-only default, both settings
    # fall back to the same placeholder string, so this test configures
    # distinct secrets explicitly to exercise the case that actually
    # matters - two different, real, per-purpose secrets in production.)
    from app.driver_auth.tokens import InvalidDriverToken
    from app.driver_auth.tokens import decode_token as decode_driver_token

    with patch("app.client_auth.tokens.settings") as mock_client_settings:
        mock_client_settings.client_jwt_secret = "real-client-secret"
        mock_client_settings.client_jwt_expiry_hours = 24
        client_token = issue_token("client-1")

    with patch("app.driver_auth.tokens.settings") as mock_driver_settings:
        mock_driver_settings.driver_jwt_secret = "real-driver-secret"
        with pytest.raises(InvalidDriverToken):
            decode_driver_token(client_token)


def test_refuses_to_start_with_default_secret_outside_development():
    with patch("app.client_auth.tokens.settings") as mock_settings:
        mock_settings.client_jwt_secret = "dev-only-insecure-secret-change-in-production"
        mock_settings.environment = "production"
        with pytest.raises(RuntimeError):
            assert_client_jwt_secret_configured()


def test_allows_default_secret_in_development():
    with patch("app.client_auth.tokens.settings") as mock_settings:
        mock_settings.client_jwt_secret = "dev-only-insecure-secret-change-in-production"
        mock_settings.environment = "development"
        assert_client_jwt_secret_configured()  # must not raise


def test_allows_a_real_secret_outside_development():
    with patch("app.client_auth.tokens.settings") as mock_settings:
        mock_settings.client_jwt_secret = "a-real-generated-secret"
        mock_settings.environment = "production"
        assert_client_jwt_secret_configured()  # must not raise


@pytest.mark.asyncio
async def test_login_is_rate_limited_after_max_attempts(fake_redis):
    limiter = LoginRateLimiter()
    for _ in range(MAX_LOGIN_ATTEMPTS):
        await limiter.check_and_increment("client@example.com")

    with pytest.raises(LoginRateLimitExceeded):
        await limiter.check_and_increment("client@example.com")


@pytest.mark.asyncio
async def test_login_rate_limit_is_per_email(fake_redis):
    limiter = LoginRateLimiter()
    for _ in range(MAX_LOGIN_ATTEMPTS):
        await limiter.check_and_increment("client@example.com")

    # A different email has its own independent budget.
    await limiter.check_and_increment("other-client@example.com")


@pytest.mark.asyncio
async def test_login_rate_limit_resets_on_success(fake_redis):
    limiter = LoginRateLimiter()
    for _ in range(MAX_LOGIN_ATTEMPTS):
        await limiter.check_and_increment("client@example.com")
    await limiter.reset("client@example.com")

    # The counter was cleared, so this doesn't raise even though it would
    # have been the (MAX_LOGIN_ATTEMPTS + 1)th attempt otherwise.
    await limiter.check_and_increment("client@example.com")
