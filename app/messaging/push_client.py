"""
Push-notification send client for new job offers (docs/ROADMAP.md A1,
app/messaging/job_offer_notifications.py).

Same "unconfigured -> stub/dev mode" shape as app/messaging/sms_client.py,
with one real difference: Expo's push service needs no account or
credential to call in the basic case (unlike Twilio), so there's nothing
to gate ExpoPushClient's selection on except a deliberate on/off switch
(EXPO_PUSH_ENABLED) - see that setting's docstring in app/config.py for the
actual remaining gap (no EAS project id configured client-side yet).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = structlog.get_logger(__name__)

EXPO_PUSH_ENDPOINT = "https://exp.host/--/api/v2/push/send"


class PushNotificationClient(ABC):
    engine_name: str

    @abstractmethod
    async def send(self, expo_push_token: str, title: str, body: str, data: dict | None = None) -> None:
        """Send one push notification. Best-effort - a failure here should
        never block the caller's own work (e.g. a dispatch cycle)."""
        raise NotImplementedError


class ExpoPushClient(PushNotificationClient):
    engine_name = "expo"

    def __init__(self, access_token: str | None = None) -> None:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        self._http = httpx.AsyncClient(timeout=5.0, headers=headers)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.25, max=1))
    async def send(self, expo_push_token: str, title: str, body: str, data: dict | None = None) -> None:
        response = await self._http.post(
            EXPO_PUSH_ENDPOINT,
            json={"to": expo_push_token, "title": title, "body": body, "data": data or {}},
        )
        response.raise_for_status()
        # Expo returns 200 even for a per-message delivery error (e.g. a
        # stale/unregistered token) - errors live in the JSON body, not the
        # HTTP status. Logged, not raised: one bad token should never make
        # the caller retry the whole batch or fail the triggering cycle.
        result = response.json().get("data", {})
        if isinstance(result, dict) and result.get("status") == "error":
            logger.warning("expo_push_send_error", detail=result.get("message"))


class StubPushClient(PushNotificationClient):
    engine_name = "stub"

    async def send(self, expo_push_token: str, title: str, body: str, data: dict | None = None) -> None:
        logger.info("stub_push_sent", token_prefix=expo_push_token[:12], title=title)


def get_push_client() -> PushNotificationClient:
    if settings.expo_push_enabled:
        logger.info("push_client_selected", engine="expo")
        return ExpoPushClient(access_token=settings.expo_push_access_token)
    logger.warning(
        "push_client_selected",
        engine="stub",
        reason="EXPO_PUSH_ENABLED is not set - running in stub mode",
    )
    return StubPushClient()
