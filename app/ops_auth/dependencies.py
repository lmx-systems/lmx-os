"""FastAPI dependency for routes that need the authenticated ops user
(e.g. GET /ops/me). Bulk gating of ops routes happens in the auth
middleware (app/security.py) instead - existing route signatures stay
untouched."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.ops_auth.tokens import InvalidOpsToken, decode_token


@dataclass(frozen=True)
class AuthedOpsUser:
    user_id: str
    role: str


async def get_current_ops_user(authorization: str | None = Header(default=None)) -> AuthedOpsUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        user_id, role = decode_token(token)
    except InvalidOpsToken:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return AuthedOpsUser(user_id=user_id, role=role)
