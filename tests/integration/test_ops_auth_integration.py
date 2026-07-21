"""
Ops auth (roadmap item S1) against real Postgres: user creation via the
admin endpoint, login issuing a role-carrying token, /ops/me, and the
deactivation path.
"""
import uuid

import pytest
from fastapi import HTTPException

from app.api.ops_auth_routes import create_ops_user, get_me, ops_login
from app.models.ops_user import OpsUser
from app.ops_auth.dependencies import AuthedOpsUser
from app.ops_auth.tokens import decode_token
from app.schemas.ops_auth import OpsLoginBody, OpsUserCreateBody

pytestmark = pytest.mark.integration


def _create_body(email: str, role: str = "operator") -> OpsUserCreateBody:
    return OpsUserCreateBody(
        email=email, name="Test Operator", password="a long enough password", role=role
    )


async def test_create_login_and_me_roundtrip(db_session, real_redis_client):
    email = f"op-{uuid.uuid4().hex[:8]}@lmx.example"
    created = await create_ops_user(_create_body(email, role="admin"), session=db_session)
    assert created.role == "admin"

    token = await ops_login(
        OpsLoginBody(email=email, password="a long enough password"), session=db_session
    )
    user_id, role = decode_token(token.access_token)
    assert user_id == created.user_id
    assert role == "admin"

    me = await get_me(
        ops_user=AuthedOpsUser(user_id=created.user_id, role=role), session=db_session
    )
    assert me.email == email


async def test_login_rejects_wrong_password_and_unknown_email(db_session, real_redis_client):
    email = f"op-{uuid.uuid4().hex[:8]}@lmx.example"
    await create_ops_user(_create_body(email), session=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await ops_login(OpsLoginBody(email=email, password="wrong password!!"), session=db_session)
    assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        await ops_login(
            OpsLoginBody(email="nobody@lmx.example", password="whatever whatever"),
            session=db_session,
        )
    assert exc_info.value.status_code == 401


async def test_duplicate_email_is_409(db_session):
    email = f"op-{uuid.uuid4().hex[:8]}@lmx.example"
    await create_ops_user(_create_body(email), session=db_session)
    with pytest.raises(HTTPException) as exc_info:
        await create_ops_user(_create_body(email), session=db_session)
    assert exc_info.value.status_code == 409


async def test_deactivated_user_cannot_login(db_session, real_redis_client):
    email = f"op-{uuid.uuid4().hex[:8]}@lmx.example"
    created = await create_ops_user(_create_body(email), session=db_session)

    user = await db_session.get(OpsUser, uuid.UUID(created.user_id))
    user.active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await ops_login(
            OpsLoginBody(email=email, password="a long enough password"), session=db_session
        )
    assert exc_info.value.status_code == 401
