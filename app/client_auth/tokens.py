"""
JWT session tokens for the client portal (Phase 8).

Mirrors app/driver_auth/tokens.py's shape (stateless JWT, claims embedded
at issuance, one fail-fast startup check) but deliberately does not share
its secret or its decode function - see app/config.py's
client_jwt_secret docstring for why a client and driver token must never
be interchangeable.
"""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ClientTokenClaims:
    # sub is the client *user*, not the client - a portal login is now
    # per-user (docs/ROADMAP.md C4). client_id/role are carried too so the
    # common read paths don't need a second lookup just to scope a query,
    # but is_active is deliberately re-checked against the DB on every
    # request (app/client_auth/dependencies.py), not trusted from the
    # token, so a deactivated user loses access immediately.
    client_user_id: str
    client_id: str
    role: str


def issue_token(client_user_id: str, client_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": client_user_id,
        "client_id": client_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.client_jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.client_jwt_secret, algorithm=ALGORITHM)


class InvalidClientToken(Exception):
    pass


def decode_token(token: str) -> ClientTokenClaims:
    """Returns the token's claims. Raises InvalidClientToken if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.client_jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidClientToken(str(exc)) from exc
    try:
        return ClientTokenClaims(
            client_user_id=payload["sub"],
            client_id=payload["client_id"],
            role=payload["role"],
        )
    except KeyError as exc:
        # A well-formed, correctly-signed token from before C4 (sub only,
        # no client_id/role) - treat as invalid so it fails closed to a
        # re-login rather than KeyError-500ing.
        raise InvalidClientToken(f"missing claim {exc}") from exc
