"""
JWT session tokens for the ops dashboard (docs/ROADMAP.md S1).

Mirrors app/client_auth/tokens.py's shape (stateless JWT, claims embedded
at issuance, one fail-fast startup check) but deliberately does not share
its secret or its decode function - see app/config.py's ops_jwt_secret
docstring for why an ops token must never be interchangeable with a
client or driver one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings

ALGORITHM = "HS256"

_INSECURE_DEFAULT_SECRET = "dev-only-insecure-secret-change-in-production"


def assert_ops_jwt_secret_configured() -> None:
    """Fail fast at boot rather than silently issuing forgeable ops
    dashboard sessions - called from app.main's lifespan alongside the
    equivalent client/driver checks."""
    if settings.ops_jwt_secret == _INSECURE_DEFAULT_SECRET and settings.environment != "development":
        raise RuntimeError(
            "OPS_JWT_SECRET is unset outside development - refusing to start. "
            "Ops dashboard sessions would be signed with a secret published in "
            "this repo's source, making them trivially forgeable - and an ops "
            "session authorizes fleet-wide read/write across every hub."
        )


def issue_token(ops_user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": ops_user_id,
        "iat": now,
        "exp": now + timedelta(hours=settings.ops_jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.ops_jwt_secret, algorithm=ALGORITHM)


class InvalidOpsToken(Exception):
    pass


def decode_token(token: str) -> str:
    """Returns ops_user_id. Raises InvalidOpsToken if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.ops_jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidOpsToken(str(exc)) from exc
    return payload["sub"]
