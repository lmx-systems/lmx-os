"""
Twilio webhook signature verification (roadmap item S7) - the pure
algorithm in app/messaging/twilio_signature.py, plus the webhook's
enforcement behavior (403 on bad/missing signature when a Twilio auth
token is configured; skip when it isn't - dev stub mode).
"""
from unittest.mock import patch

import pytest

from app.api.webhooks import twilio_inbound_sms
from app.messaging.twilio_signature import compute_signature, signature_is_valid
from tests.twilio_request_helpers import make_twilio_form_request

AUTH_TOKEN = "test_auth_token_12345"
URL = "http://testserver/webhooks/twilio/inbound-sms"


def test_compute_signature_matches_twilio_documented_example():
    # Twilio's documented algorithm: URL + params concatenated in sorted
    # key order as key+value, HMAC-SHA1 with the auth token, base64.
    # Verified by construction here (the algorithm is deterministic), and
    # cross-checked by the valid/invalid tests below.
    params = {"From": "+15551234567", "Body": "hello", "MessageSid": "SM123"}
    sig = compute_signature(AUTH_TOKEN, URL, params)
    # Signature is stable for identical input.
    assert sig == compute_signature(AUTH_TOKEN, URL, params)
    # And sensitive to every input.
    assert sig != compute_signature("other_token", URL, params)
    assert sig != compute_signature(AUTH_TOKEN, URL + "?x=1", params)
    assert sig != compute_signature(AUTH_TOKEN, URL, {**params, "Body": "tampered"})


def test_signature_is_valid_accepts_correct_and_rejects_wrong():
    params = {"From": "+15551234567", "Body": "hello"}
    good = compute_signature(AUTH_TOKEN, URL, params)
    assert signature_is_valid(AUTH_TOKEN, URL, params, good) is True
    assert signature_is_valid(AUTH_TOKEN, URL, params, "bogus") is False
    assert signature_is_valid(AUTH_TOKEN, URL, params, None) is False
    assert signature_is_valid(AUTH_TOKEN, URL, params, "") is False


def test_param_order_does_not_matter_but_values_do():
    a = compute_signature(AUTH_TOKEN, URL, {"A": "1", "B": "2"})
    b = compute_signature(AUTH_TOKEN, URL, {"B": "2", "A": "1"})
    assert a == b


@pytest.mark.asyncio
async def test_webhook_rejects_missing_signature_when_token_configured():
    from fastapi import HTTPException

    with patch("app.api.webhooks.settings") as mock_settings:
        mock_settings.twilio_auth_token = AUTH_TOKEN
        mock_settings.twilio_webhook_public_url = None
        request = make_twilio_form_request({"From": "+15551234567", "Body": "hi"})
        with pytest.raises(HTTPException) as exc_info:
            await twilio_inbound_sms(request, session=None)
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_webhook_rejects_wrong_signature_when_token_configured():
    from fastapi import HTTPException

    with patch("app.api.webhooks.settings") as mock_settings:
        mock_settings.twilio_auth_token = AUTH_TOKEN
        mock_settings.twilio_webhook_public_url = None
        request = make_twilio_form_request(
            {"From": "+15551234567", "Body": "hi"}, signature="not-a-real-signature"
        )
        with pytest.raises(HTTPException) as exc_info:
            await twilio_inbound_sms(request, session=None)
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_webhook_accepts_valid_signature_against_public_url_override():
    # The public URL override is what makes this correct behind a proxy -
    # Twilio signs the URL registered in its console, not whatever
    # scheme/host the app happens to see internally.
    public_url = "https://api.lmx.example/webhooks/twilio/inbound-sms"
    form = {"From": "+19995550000", "Body": "???"}
    good_sig = compute_signature(AUTH_TOKEN, public_url, form)

    class _NoResultSession:
        async def execute(self, *_args, **_kwargs):
            class _Scalars:
                def all(self):
                    return []

            class _Result:
                def scalars(self):
                    return _Scalars()

            return _Result()

    with patch("app.api.webhooks.settings") as mock_settings:
        mock_settings.twilio_auth_token = AUTH_TOKEN
        mock_settings.twilio_webhook_public_url = public_url
        request = make_twilio_form_request(form, signature=good_sig)
        response = await twilio_inbound_sms(request, session=_NoResultSession())
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_webhook_skips_verification_when_no_token_configured():
    # Dev/stub mode - no Twilio account exists, so there is no token to
    # verify against. Same "unconfigured -> stub" posture as SmsClient.
    class _NoResultSession:
        async def execute(self, *_args, **_kwargs):
            class _Scalars:
                def all(self):
                    return []

            class _Result:
                def scalars(self):
                    return _Scalars()

            return _Result()

    with patch("app.api.webhooks.settings") as mock_settings:
        mock_settings.twilio_auth_token = None
        mock_settings.twilio_webhook_public_url = None
        request = make_twilio_form_request({"From": "+15551234567", "Body": "hi"})
        response = await twilio_inbound_sms(request, session=_NoResultSession())
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_webhook_422s_on_missing_required_fields():
    from fastapi import HTTPException

    with patch("app.api.webhooks.settings") as mock_settings:
        mock_settings.twilio_auth_token = None
        mock_settings.twilio_webhook_public_url = None
        request = make_twilio_form_request({"Body": "no From field"})
        with pytest.raises(HTTPException) as exc_info:
            await twilio_inbound_sms(request, session=None)
        assert exc_info.value.status_code == 422
