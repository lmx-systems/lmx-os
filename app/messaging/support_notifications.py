"""
Telling a driver that dispatch answered them (screens 1p/1q).

The reply itself is a `Message` row the app's support screen reads; this is the
nudge for a driver who isn't looking at it. Push reaches only the driver's own
signed-in phones, so no SMS provider is involved.

Best-effort: with no push configured, or a phone that never registered, the
driver sees the reply the next time they open the support screen.
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from app.db import session_scope
from app.messaging.push_client import get_push_client
from app.models.driver_device import DriverDevice

logger = structlog.get_logger(__name__)

_PREVIEW_CHARS = 120


async def notify_driver_of_support_reply(driver_id: uuid.UUID, body: str) -> None:
    """Never raises: the reply is already saved, and a failed nudge must not
    make the console think it wasn't (and send it again)."""
    try:
        await _notify(driver_id, body)
    except Exception:
        logger.exception("support_reply_push_failed", driver_id=str(driver_id))


async def _notify(driver_id: uuid.UUID, body: str) -> None:
    async with session_scope() as session:
        result = await session.execute(
            select(DriverDevice.expo_push_token).where(
                DriverDevice.driver_id == driver_id,
                DriverDevice.revoked_at.is_(None),
                DriverDevice.expo_push_token.isnot(None),
            )
        )
        tokens = [row[0] for row in result.all() if row[0] is not None]

    preview = body if len(body) <= _PREVIEW_CHARS else body[: _PREVIEW_CHARS - 1] + "…"
    client = get_push_client()
    for token in tokens:
        try:
            await client.send(token, "Dispatch replied", preview, data={"type": "support_reply"})
        except Exception:
            logger.exception("support_reply_push_failed", driver_id=str(driver_id))
