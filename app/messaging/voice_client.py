"""
Masked voice calling (docs/ROADMAP.md A7) - same "unconfigured credential
-> stub" shape as app/messaging/sms_client.py, using Twilio's Calls API
instead of Messages.

Masking here works by bridging two real phone calls, not in-app audio:
Twilio first calls the driver's own phone (`place_masked_call`'s
`driver_phone`); once they answer, Twilio requests TwiML from
`connect_url` (app/api/webhooks.py's `voice_connect`), which tells it to
<Dial> the customer's real number with callerId set to LMX's shared
Twilio number. So the customer always sees LMX's number, never the
driver's personal one, and the driver never sees or dials the customer's
real number directly - it's stored server-side only (app/models/call.py's
`counterparty_phone`), same non-negotiable as masked SMS.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = structlog.get_logger(__name__)

TWILIO_CALLS_ENDPOINT = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"


class VoiceClient(ABC):
    engine_name: str

    @abstractmethod
    async def place_masked_call(self, *, driver_phone: str, connect_url: str, status_callback_url: str) -> str | None:
        """Place the driver-leg call, returning the provider's call SID (or
        None if there isn't one - e.g. the stub client)."""
        raise NotImplementedError


class TwilioVoiceClient(VoiceClient):
    engine_name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self._account_sid = account_sid
        self._from_number = from_number
        self._http = httpx.AsyncClient(timeout=5.0, auth=(account_sid, auth_token))

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.25, max=1))
    async def place_masked_call(self, *, driver_phone: str, connect_url: str, status_callback_url: str) -> str | None:
        response = await self._http.post(
            TWILIO_CALLS_ENDPOINT.format(account_sid=self._account_sid),
            data={
                "To": driver_phone,
                "From": self._from_number,
                "Url": connect_url,
                "StatusCallback": status_callback_url,
                "StatusCallbackEvent": "completed",
            },
        )
        response.raise_for_status()
        return response.json().get("sid")


class StubVoiceClient(VoiceClient):
    engine_name = "stub"

    async def place_masked_call(self, *, driver_phone: str, connect_url: str, status_callback_url: str) -> str | None:
        logger.info("stub_call_placed", driver_phone=driver_phone)
        return None


def get_voice_client() -> VoiceClient:
    if settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number:
        logger.info("voice_client_selected", engine="twilio")
        return TwilioVoiceClient(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_from_number,
        )
    logger.warning(
        "voice_client_selected",
        engine="stub",
        reason="TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM_NUMBER not fully configured - running in stub mode",
    )
    return StubVoiceClient()
