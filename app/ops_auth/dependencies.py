"""
Authenticates an ops-dashboard request via Bearer JWT. Unlike
app/client_auth/dependencies.py / app/driver_auth/dependencies.py (pure
JWT-decode, no DB hit), this also checks OpsUser.is_active on every
request, not just at login - so revoking an ops user's access mid-session
(e.g. someone who's left) takes effect immediately instead of waiting for
their token to expire on its own. Ops sessions authorize fleet-wide
read/write across every hub, which makes that gap worth the extra DB
round trip that client/driver sessions don't pay.

Exposed two ways: `authenticate` is the shared logic, used by both
`get_current_ops_user` (a normal FastAPI dependency, for the one route -
GET /ops/me - that needs to know *which* ops user is asking) and
app/ops_auth/middleware.py's OpsUserAuthMiddleware (a blanket gate over
the rest of the internal API, which doesn't need per-user identity
injected into every handler - just a yes/no).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, session_scope
from app.models.ops_user import OpsUser
from app.ops_auth.tokens import InvalidOpsToken, decode_token


@dataclass(frozen=True)
class AuthedOpsUser:
    ops_user_id: str
    email: str
    name: str


class InvalidOpsSession(Exception):
    pass


async def authenticate(token: str, session: AsyncSession) -> AuthedOpsUser:
    try:
        ops_user_id = decode_token(token)
    except InvalidOpsToken as exc:
        raise InvalidOpsSession(str(exc)) from exc

    row = await session.get(OpsUser, uuid.UUID(ops_user_id))
    if row is None or not row.is_active:
        raise InvalidOpsSession("Ops user not found or deactivated")

    return AuthedOpsUser(ops_user_id=str(row.id), email=row.email, name=row.name)


async def authenticate_token(token: str) -> AuthedOpsUser:
    """Same as `authenticate`, opening its own short-lived session via
    session_scope() - for callers outside FastAPI's request-scoped
    Depends(get_db) chain, i.e. OpsUserAuthMiddleware."""
    async with session_scope() as session:
        return await authenticate(token, session)


async def get_current_ops_user(
    authorization: str | None = Header(default=None), session: AsyncSession = Depends(get_db)
) -> AuthedOpsUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        return await authenticate(token, session)
    except InvalidOpsSession:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
