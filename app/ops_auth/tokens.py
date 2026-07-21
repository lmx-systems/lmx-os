"""
JWT session tokens for ops dashboard users (roadmap item S1).

Third token surface, third secret - deliberately distinct from both
driver_jwt_secret and client_jwt_secret (app/config.py's
assert_jwt_secrets_are_distinct now checks all three pairwise) so no
token from one audience can ever validate as another. Carries the user's
role as a claim: the auth middleware (app/security.py) uses it to gate
/admin/* to admins without a DB read per request. Role changes therefore
take effect on next login (bounded by the token's expiry, deliberately
short at 12h) - acceptable for an internal tool; revocation-on-read is a
later upgrade if it ever matters.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings

ALGORITHM = "HS256"

_INSECURE_DEFAULT_SECRET = "dev-only-insecure-secret-change-in-production"

ROLES = ("admin", "operator")


def assert_ops_jwt_secret_configured() -> None:
    if settings.ops_jwt_secret == _INSECURE_DEFAULT_SECRET and settings.environment != "development":
        raise RuntimeError(
            "OPS_JWT_SECRET is unset outside development - refusing to start. "
            "Ops dashboard sessions would be signed with a secret published "
            "in this repo's source, making them trivially forgeable."
        )


def issue_token(user_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.ops_jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.ops_jwt_secret, algorithm=ALGORITHM)


class InvalidOpsToken(Exception):
    pass


def decode_token(token: str) -> tuple[str, str]:
    """Returns (user_id, role). Raises InvalidOpsToken if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.ops_jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidOpsToken(str(exc)) from exc
    return payload["sub"], payload.get("role", "operator")
