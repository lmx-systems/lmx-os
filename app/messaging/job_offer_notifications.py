"""
Sends a push notification the moment a driver receives a new job offer
(docs/ROADMAP.md A1) - called from app/optimizer/service.py right after a
RouteOffer is committed. Without this, a driver only ever sees a new offer
by having the app open and polling (driver-app/src/screens/useTodayRoute.ts),
which misses it entirely while backgrounded or killed.
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from app.db import session_scope
from app.messaging.push_client import get_push_client
from app.models.driver_device import DriverDevice

logger = structlog.get_logger(__name__)


def _offer_notification_copy(stop_count: int, ttl_seconds: int) -> tuple[str, str]:
    stop_word = "stop" if stop_count == 1 else "stops"
    ttl_minutes = max(1, ttl_seconds // 60)
    return (
        "New delivery offer",
        f"{stop_count} {stop_word} nearby - respond within {ttl_minutes} min",
    )


async def notify_driver_of_new_offer(driver_id: str, stop_count: int, ttl_seconds: int) -> None:
    """Best-effort - a driver with no registered device (or an unconfigured
    push client, see get_push_client()) simply gets no push; the offer
    itself is unaffected either way, since it already exists as a real
    RouteOffer row the app will pick up on its next poll regardless."""
    async with session_scope() as session:
        result = await session.execute(
            select(DriverDevice.expo_push_token).where(
                DriverDevice.driver_id == uuid.UUID(driver_id),
                DriverDevice.revoked_at.is_(None),
                DriverDevice.expo_push_token.isnot(None),
            )
        )
        tokens = [row[0] for row in result.all()]

    if not tokens:
        return

    title, body = _offer_notification_copy(stop_count, ttl_seconds)
    client = get_push_client()
    for token in tokens:
        try:
            await client.send(token, title, body, data={"type": "job_offer"})
        except Exception:
            # A dispatch cycle already assigned the job and created the
            # real RouteOffer row before this ever runs (see
            # app/optimizer/service.py) - a push-send failure must never
            # look like the offer itself failed.
            logger.exception("job_offer_push_send_failed", driver_id=driver_id)
