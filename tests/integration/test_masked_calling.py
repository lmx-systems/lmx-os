"""
Masked voice calling (docs/ROADMAP.md A7) against real Postgres/Redis -
call_customer places the driver-leg call (stubbed here, no real Twilio
account configured in tests - same "unconfigured -> StubVoiceClient"
status test_driver_messaging_and_earnings.py already documents for SMS)
and logs a real Call row; the two Twilio callback webhooks
(voice_connect, voice_status) update it exactly as a real call would.

Reuses test_driver_app_integration.py's _seed/_accept_one_offer helpers
to get a real accepted route with a dropoff whose order has a real
Order.delivery_contact_phone, same pattern
test_driver_messaging_and_earnings.py already established for this
Phase 3 test family.
"""
import uuid
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from app.api.driver_routes import call_customer
from app.api.webhooks import voice_connect, voice_status
from app.config import settings
from app.messaging.twilio_signature import compute_signature
from app.models.call import Call
from tests.integration.test_driver_app_integration import _accept_one_offer, _seed

pytestmark = pytest.mark.integration

TEST_AUTH_TOKEN = "test-auth-token"


@pytest.fixture
def twilio_signs_requests(monkeypatch):
    """Configure the auth token, so a request signed with it is Twilio's."""
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    monkeypatch.setattr(settings, "twilio_webhook_base_url", None)


def _fake_twilio_request(path: str, *, signed: bool = True) -> Request:
    """A minimal real Starlette Request whose .form()/.url the voice
    webhooks can exercise, signed the way Twilio signs it unless told not
    to. The form is empty, so the signature covers the URL alone."""
    body = urlencode({}).encode()
    headers = [(b"content-type", b"application/x-www-form-urlencoded")]
    if signed:
        signature = compute_signature(TEST_AUTH_TOKEN, f"http://testserver{path}", {})
        headers.append((b"x-twilio-signature", signature.encode()))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": headers,
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


async def test_call_customer_logs_a_real_call_with_masked_number(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, _pickup, dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    view = await call_customer(dropoff.stop_id, driver=authed, session=db_session)
    assert view.status == "initiated"

    result = await db_session.execute(select(Call).where(Call.id == uuid.UUID(view.call_id)))
    call = result.scalar_one()
    assert call.counterparty_phone == order.delivery_contact_phone
    assert str(call.stop_id) == dropoff.stop_id
    # StubVoiceClient (no Twilio account configured in tests) never places
    # a real call, so there's no SID to record - same status as
    # StubSmsClient elsewhere in this test suite.
    assert call.twilio_call_sid is None


async def test_call_customer_rejects_a_pickup_stop(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, pickup, _dropoff = await _accept_one_offer(db_session, hub_id, driver_id)

    with pytest.raises(HTTPException) as exc_info:
        await call_customer(pickup.stop_id, driver=authed, session=db_session)
    assert exc_info.value.status_code == 409


async def test_voice_connect_webhook_bridges_to_the_real_customer_number(
    db_session, real_redis_client, twilio_signs_requests
):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, _pickup, dropoff = await _accept_one_offer(db_session, hub_id, driver_id)
    view = await call_customer(dropoff.stop_id, driver=authed, session=db_session)

    request = _fake_twilio_request(f"/webhooks/twilio/voice-connect/{view.call_id}")
    response = await voice_connect(view.call_id, request, session=db_session)
    assert order.delivery_contact_phone.encode() in response.body
    assert b"<Dial" in response.body

    result = await db_session.execute(select(Call).where(Call.id == uuid.UUID(view.call_id)))
    assert result.scalar_one().status == "connected"


async def test_voice_connect_refuses_an_unsigned_request_and_keeps_the_number_masked(
    db_session, real_redis_client
):
    """The TwiML this returns carries the customer's real number. With no
    TWILIO_AUTH_TOKEN it used to answer anyone, so anyone holding a call id
    could unmask the customer."""
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, _pickup, dropoff = await _accept_one_offer(db_session, hub_id, driver_id)
    view = await call_customer(dropoff.stop_id, driver=authed, session=db_session)

    request = _fake_twilio_request(f"/webhooks/twilio/voice-connect/{view.call_id}", signed=False)
    with pytest.raises(HTTPException) as exc_info:
        await voice_connect(view.call_id, request, session=db_session)
    assert exc_info.value.status_code == 403

    result = await db_session.execute(select(Call).where(Call.id == uuid.UUID(view.call_id)))
    assert result.scalar_one().status == "initiated"


async def test_voice_status_webhook_records_final_status_and_duration(
    db_session, real_redis_client, twilio_signs_requests
):
    hub_id, client_id, shop_id, driver_id, order = await _seed(db_session)
    authed, _pickup, dropoff = await _accept_one_offer(db_session, hub_id, driver_id)
    view = await call_customer(dropoff.stop_id, driver=authed, session=db_session)

    request = _fake_twilio_request(f"/webhooks/twilio/voice-status/{view.call_id}")
    await voice_status(view.call_id, request, CallStatus="completed", CallDuration="42", session=db_session)

    result = await db_session.execute(select(Call).where(Call.id == uuid.UUID(view.call_id)))
    call = result.scalar_one()
    assert call.status == "completed"
    assert call.duration_seconds == 42
