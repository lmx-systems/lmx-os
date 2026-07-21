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

from app.config import settings

ALGORITHM = "HS256"

_INSECURE_DEFAULT_SECRET = "dev-only-insecure-secret-change-in-production"


def assert_driver_jwt_secret_configured() -> None:
    """Fail fast at boot rather than silently issuing forgeable driver
    sessions - called once from app.main's lifespan, alongside the existing
    Postgres/Redis reachability checks."""
    if settings.driver_jwt_secret == _INSECURE_DEFAULT_SECRET and settings.environment != "development":
        raise RuntimeError(
            "DRIVER_JWT_SECRET is unset outside development - refusing to start. "
            "Driver sessions would be signed with a secret published in this "
            "repo's source, making them trivially forgeable."
        )


def issue_token(driver_id: str, hub_id: str, device_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": driver_id,
        "hub_id": hub_id,
        "device_id": device_id,
        "iat": now,
        "exp": now + timedelta(hours=settings.driver_jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.driver_jwt_secret, algorithm=ALGORITHM)


class InvalidDriverToken(Exception):
    pass


def decode_token(token: str) -> tuple[str, str, str]:
    """Returns (driver_id, hub_id, device_id). Raises InvalidDriverToken if
    invalid/expired/missing the device_id claim (e.g. a token issued before
    device-bound auth existed - re-authenticating issues a current one)."""
    try:
        payload = jwt.decode(token, settings.driver_jwt_secret, algorithms=[ALGORITHM])
        return payload["sub"], payload["hub_id"], payload["device_id"]
    except (jwt.PyJWTError, KeyError) as exc:
        raise InvalidDriverToken(str(exc)) from exc
