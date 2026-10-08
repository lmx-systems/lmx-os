"""
Sign-in code format and JWT token round-trip - the pieces of app/driver_auth/
that don't need a real Postgres driver row to exercise. Issuing and redeeming
codes against the database is tests/integration/test_driver_sign_in_codes.py.
"""
from unittest.mock import patch

import pytest
from fakeredis import aioredis as fakeredis_aioredis

import app.driver_auth.sign_in_codes as sign_in_codes_module
from app.driver_auth.sign_in_codes import (
    ALPHABET,
    CODE_LENGTH,
    MAX_ATTEMPTS_PER_IP,
    IssuedCode,
    SignInAttemptsExceeded,
    charge_attempt,
    code_hmac,
    normalize,
)
from app.driver_auth.tokens import (
    InvalidDriverToken,
    assert_driver_jwt_secret_configured,
    decode_token,
    issue_token,
)


def test_a_code_has_about_fifty_bits():
    """The texted code was four digits: guessable in about a day under its own
    limits. Ten characters from 31 is not."""
    assert CODE_LENGTH == 10 and len(ALPHABET) == 31
    assert len(ALPHABET) ** CODE_LENGTH > 2**49


def test_the_alphabet_leaves_out_characters_people_misread():
    assert not set("01OIL") & set(ALPHABET)


def test_new_codes_use_only_the_alphabet_and_differ():
    codes = {sign_in_codes_module._new_code() for _ in range(200)}
    assert len(codes) == 200
    assert all(len(c) == CODE_LENGTH and set(c) <= set(ALPHABET) for c in codes)


def test_what_a_driver_types_or_scans_normalizes_to_the_code():
    issued = IssuedCode(code="ABCDE23456", expires_at=None)  # type: ignore[arg-type]
    assert issued.display == "ABCDE-23456"
    for typed in ("ABCDE23456", "abcde-23456", " ABCDE 23456 ", issued.qr_payload, issued.display):
        assert normalize(typed) == "ABCDE23456"


def test_only_a_keyed_hash_is_stored(monkeypatch):
    """A plain hash of a short code could be reversed by trying every code; the
    key makes that need the server's secret too."""
    digest = code_hmac("ABCDE23456")
    assert "ABCDE23456" not in digest and len(digest) == 64
    monkeypatch.setattr(sign_in_codes_module.settings, "driver_jwt_secret", "another-secret")
    assert code_hmac("ABCDE23456") != digest


@pytest.mark.asyncio
async def test_sign_in_attempts_are_capped_per_address(monkeypatch):
    client = fakeredis_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(sign_in_codes_module, "get_client", lambda: client)
    for _ in range(MAX_ATTEMPTS_PER_IP):
        await charge_attempt("203.0.113.9")
    with pytest.raises(SignInAttemptsExceeded):
        await charge_attempt("203.0.113.9")
    # Another address has its own budget.
    await charge_attempt("203.0.113.10")


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
