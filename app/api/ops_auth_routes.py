"""
Ops-dashboard auth (docs/ROADMAP.md S1) - real per-account login for
dashboard/, replacing the shared X-API-Key stopgap. Mirrors
app/api/client_routes.py's login shape exactly.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.client_auth.passwords import verify_password
from app.db import get_db
from app.models.ops_user import OpsUser
from app.ops_auth.dependencies import AuthedOpsUser, get_current_ops_user
from app.ops_auth.login_rate_limit import LoginRateLimitExceeded, LoginRateLimiter
from app.ops_auth.tokens import issue_token
from app.schemas.ops_auth import OpsAuthToken, OpsLoginBody, OpsProfileView

router = APIRouter(prefix="/ops", tags=["ops-auth"])


@router.post("/auth/login", response_model=OpsAuthToken)
async def login(body: OpsLoginBody, session: AsyncSession = Depends(get_db)) -> OpsAuthToken:
    limiter = LoginRateLimiter()
    try:
        await limiter.check_and_increment(body.email)
    except LoginRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    result = await session.execute(select(OpsUser).where(OpsUser.email == body.email))
    ops_user = result.scalar_one_or_none()

    # Same error either way (unknown email, wrong password, or a
    # deactivated account) - don't leak which part was wrong, and don't
    # tell an unauthenticated caller a given email even has an account.
    if ops_user is None or not ops_user.is_active or not verify_password(body.password, ops_user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await limiter.reset(body.email)
    return OpsAuthToken(access_token=issue_token(str(ops_user.id)))


@router.get("/me", response_model=OpsProfileView)
async def get_my_profile(ops_user: AuthedOpsUser = Depends(get_current_ops_user)) -> OpsProfileView:
    return OpsProfileView(ops_user_id=ops_user.ops_user_id, email=ops_user.email, name=ops_user.name)
