"""
JWT session tokens for the client portal (Phase 8).

Mirrors app/driver_auth/tokens.py's shape (stateless JWT, claims embedded
at issuance, one fail-fast startup check) but deliberately does not share
its secret or its decode function - see app/config.py's
client_jwt_secret docstring for why a client and driver token must never
be interchangeable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings

ALGORITHM = "HS256"

_INSECURE_DEFAULT_SECRET = "dev-only-insecure-secret-change-in-production"


def assert_client_jwt_secret_configured() -> None:
    """Fail fast at boot rather than silently issuing forgeable client
    portal sessions - called from app.main's lifespan alongside the
    equivalent driver-app check."""
    if settings.client_jwt_secret == _INSECURE_DEFAULT_SECRET and settings.environment != "development":
        raise RuntimeError(
            "CLIENT_JWT_SECRET is unset outside development - refusing to start. "
            "Client portal sessions would be signed with a secret published in "
            "this repo's source, making them trivially forgeable."
        )


def issue_token(client_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": client_id,
        "iat": now,
        "exp": now + timedelta(hours=settings.client_jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.client_jwt_secret, algorithm=ALGORITHM)


class InvalidClientToken(Exception):
    pass


def decode_token(token: str) -> str:
    """Returns client_id. Raises InvalidClientToken if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.client_jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidClientToken(str(exc)) from exc
    return payload["sub"]
