"""
Ops-dashboard auth (docs/ROADMAP.md S1) - the pieces of app/ops_auth/
that don't need a real Postgres OpsUser row to exercise: JWT token
round-trip and login rate limiting. Mirrors tests/test_client_auth.py's
coverage for the equivalent client-portal pieces (password hashing itself
is shared - see app/client_auth/passwords.py's docstring - and already
covered there, so it isn't duplicated here).
"""
from unittest.mock import patch

import pytest
from fakeredis import aioredis as fakeredis_aioredis

import app.ops_auth.login_rate_limit as ops_login_rate_limit_module
from app.ops_auth.login_rate_limit import MAX_LOGIN_ATTEMPTS, LoginRateLimitExceeded, LoginRateLimiter
from app.ops_auth.tokens import InvalidOpsToken, assert_ops_jwt_secret_configured, decode_token, issue_token


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(ops_login_rate_limit_module, "get_client", lambda: client)
    return client


def test_issue_and_decode_token_roundtrip():
    token = issue_token("ops-user-1")
    assert decode_token(token) == "ops-user-1"


def test_decode_rejects_garbage_token():
    with pytest.raises(InvalidOpsToken):
        decode_token("not-a-real-token")


def test_ops_and_client_tokens_are_not_interchangeable_with_distinct_secrets():
    from app.client_auth.tokens import InvalidClientToken
    from app.client_auth.tokens import decode_token as decode_client_token

    with patch("app.ops_auth.tokens.settings") as mock_ops_settings:
        mock_ops_settings.ops_jwt_secret = "real-ops-secret"
        mock_ops_settings.ops_jwt_expiry_hours = 24
        ops_token = issue_token("ops-user-1")

    with patch("app.client_auth.tokens.settings") as mock_client_settings:
        mock_client_settings.client_jwt_secret = "real-client-secret"
        with pytest.raises(InvalidClientToken):
            decode_client_token(ops_token)


def test_refuses_to_start_with_default_secret_outside_development():
    with patch("app.ops_auth.tokens.settings") as mock_settings:
        mock_settings.ops_jwt_secret = "dev-only-insecure-secret-change-in-production"
        mock_settings.environment = "production"
        with pytest.raises(RuntimeError):
            assert_ops_jwt_secret_configured()


def test_allows_default_secret_in_development():
    with patch("app.ops_auth.tokens.settings") as mock_settings:
        mock_settings.ops_jwt_secret = "dev-only-insecure-secret-change-in-production"
        mock_settings.environment = "development"
        assert_ops_jwt_secret_configured()  # must not raise


def test_allows_a_real_secret_outside_development():
    with patch("app.ops_auth.tokens.settings") as mock_settings:
        mock_settings.ops_jwt_secret = "a-real-generated-secret"
        mock_settings.environment = "production"
        assert_ops_jwt_secret_configured()  # must not raise


@pytest.mark.asyncio
async def test_login_is_rate_limited_after_max_attempts(fake_redis):
    limiter = LoginRateLimiter()
    for _ in range(MAX_LOGIN_ATTEMPTS):
        await limiter.check_and_increment("ops@example.com")

    with pytest.raises(LoginRateLimitExceeded):
        await limiter.check_and_increment("ops@example.com")


@pytest.mark.asyncio
async def test_login_rate_limit_is_per_email(fake_redis):
    limiter = LoginRateLimiter()
    for _ in range(MAX_LOGIN_ATTEMPTS):
        await limiter.check_and_increment("ops@example.com")

    # A different email has its own independent budget.
    await limiter.check_and_increment("other-ops@example.com")


@pytest.mark.asyncio
async def test_login_rate_limit_resets_on_success(fake_redis):
    limiter = LoginRateLimiter()
    for _ in range(MAX_LOGIN_ATTEMPTS):
        await limiter.check_and_increment("ops@example.com")
    await limiter.reset("ops@example.com")

    # The counter was cleared, so this doesn't raise even though it would
    # have been the (MAX_LOGIN_ATTEMPTS + 1)th attempt otherwise.
    await limiter.check_and_increment("ops@example.com")
