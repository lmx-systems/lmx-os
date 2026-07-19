"""
JWT session tokens for the driver app.

Stateless on purpose - no server-side session table to look up on every
request. driver_id and hub_id are embedded as claims at issuance, so a
driver reassigned to a different hub won't see it reflected until they log
in again (token expiry, settings.driver_jwt_expiry_hours, is the ceiling on
how stale that can get). That's an acceptable trade-off for v1 given hub
reassignment is rare; flagging it here so it isn't a silent surprise later.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

ALGORITHM = "HS256"

_warned_insecure_secret = False


def _check_secret_configured() -> None:
    global _warned_insecure_secret
    if _warned_insecure_secret:
        return
    if settings.driver_jwt_secret == "dev-only-insecure-secret-change-in-production" and (
        settings.environment != "development"
    ):
        logger.warning(
            "driver_jwt_secret_not_configured",
            reason="DRIVER_JWT_SECRET unset outside development - driver sessions are forgeable",
        )
    _warned_insecure_secret = True


def issue_token(driver_id: str, hub_id: str) -> str:
    _check_secret_configured()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": driver_id,
        "hub_id": hub_id,
        "iat": now,
        "exp": now + timedelta(hours=settings.driver_jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.driver_jwt_secret, algorithm=ALGORITHM)


class InvalidDriverToken(Exception):
    pass


def decode_token(token: str) -> tuple[str, str]:
    """Returns (driver_id, hub_id). Raises InvalidDriverToken if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.driver_jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidDriverToken(str(exc)) from exc
    return payload["sub"], payload["hub_id"]
