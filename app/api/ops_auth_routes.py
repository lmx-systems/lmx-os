"""
Ops dashboard auth (roadmap item S1): per-user login for hub staff, with
roles, replacing sole reliance on the shared X-API-Key stopgap.

/ops/* is exempt from SharedSecretAuthMiddleware (app/security.py) the
same way /client and /driver are - it carries its own real auth. User
management lives under /admin/ops-users, which is NOT exempt: creating
users requires either the shared secret (the bootstrap path for the very
first admin) or an admin-role ops token (the steady state).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.client_auth.login_rate_limit import LoginRateLimiter, LoginRateLimitExceeded
from app.client_auth.passwords import hash_password, verify_password
from app.db import get_db
from app.models.ops_user import OpsUser
from app.ops_auth.dependencies import AuthedOpsUser, get_current_ops_user
from app.ops_auth.tokens import ROLES, issue_token
from app.schemas.ops_auth import OpsAuthToken, OpsLoginBody, OpsUserCreateBody, OpsUserView

router = APIRouter(prefix="/ops", tags=["ops-auth"])
admin_users_router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/auth/login", response_model=OpsAuthToken)
async def ops_login(body: OpsLoginBody, session: AsyncSession = Depends(get_db)) -> OpsAuthToken:
    limiter = LoginRateLimiter(key_prefix="ops_auth")
    try:
        await limiter.check_and_increment(body.email)
    except LoginRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    result = await session.execute(select(OpsUser).where(OpsUser.email == body.email))
    user = result.scalar_one_or_none()

    # Same error for unknown email / wrong password / deactivated account -
    # don't tell an unauthenticated caller which one it was.
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await limiter.reset(body.email)
    return OpsAuthToken(access_token=issue_token(str(user.id), user.role), role=user.role)


@router.get("/me", response_model=OpsUserView)
async def get_me(
    ops_user: AuthedOpsUser = Depends(get_current_ops_user),
    session: AsyncSession = Depends(get_db),
) -> OpsUserView:
    user = await session.get(OpsUser, uuid.UUID(ops_user.user_id))
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="Account no longer active")
    return OpsUserView(
        user_id=str(user.id), email=user.email, name=user.name, role=user.role, active=user.active
    )


@admin_users_router.post("/ops-users", response_model=OpsUserView, status_code=201)
async def create_ops_user(
    body: OpsUserCreateBody, session: AsyncSession = Depends(get_db)
) -> OpsUserView:
    """Create an ops user. Auth is enforced by the middleware, not here:
    /admin/* requires the shared secret (bootstrap - how the first admin
    gets created) or an admin-role ops token (see app/security.py)."""
    if body.role not in ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of {ROLES}")

    existing = await session.execute(select(OpsUser).where(OpsUser.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="An ops user with this email already exists")

    user = OpsUser(
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    session.add(user)
    await session.commit()
    return OpsUserView(
        user_id=str(user.id), email=user.email, name=user.name, role=user.role, active=user.active
    )
