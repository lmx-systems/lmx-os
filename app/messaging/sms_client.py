"""
SMS send client for driver messaging (Phase 3, screens 1p/1q - masked
customer contact and dispatch/support contact, app/models/message.py).

Same "unconfigured third-party credential -> stub/dev mode" pattern as
app/optimizer/google_routes_client.py: TwilioSmsClient is the real
implementation, used once TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/
TWILIO_FROM_NUMBER are configured (no Twilio account exists yet as of
this pass - see docs/NEXT_STEPS.md). Until then, StubSmsClient logs the
message and returns no SID, so the rest of the messaging feature
(storage, the driver app's message-thread UI, inbound webhook matching)
is fully buildable and testable without a live Twilio account.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = structlog.get_logger(__name__)

TWILIO_MESSAGES_ENDPOINT = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"


class SmsClient(ABC):
    engine_name: str

    @abstractmethod
    async def send(self, to: str, body: str) -> str | None:
        """Send an SMS, returning the provider's message SID (or None if
        there isn't one - e.g. the stub client)."""
        raise NotImplementedError


class TwilioSmsClient(SmsClient):
    engine_name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self._account_sid = account_sid
        self._from_number = from_number
        self._http = httpx.AsyncClient(timeout=5.0, auth=(account_sid, auth_token))

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.25, max=1))
    async def send(self, to: str, body: str) -> str | None:
        response = await self._http.post(
            TWILIO_MESSAGES_ENDPOINT.format(account_sid=self._account_sid),
            data={"To": to, "From": self._from_number, "Body": body},
        )
        response.raise_for_status()
        return response.json().get("sid")


class StubSmsClient(SmsClient):
    engine_name = "stub"

    async def send(self, to: str, body: str) -> str | None:
        logger.info("stub_sms_sent", to=to, body_length=len(body))
        return None


def get_sms_client() -> SmsClient:
    if settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number:
        logger.info("sms_client_selected", engine="twilio")
        return TwilioSmsClient(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_from_number,
        )
    logger.warning(
        "sms_client_selected",
        engine="stub",
        reason="TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM_NUMBER not fully configured - running in stub mode",
    )
    return StubSmsClient()
