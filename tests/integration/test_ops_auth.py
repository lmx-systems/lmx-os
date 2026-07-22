"""
Integration coverage for real per-account ops auth (docs/ROADMAP.md S1) -
login (needs a real Postgres OpsUser row) and OpsUserAuthMiddleware
(needs a real Redis-backed... no, actually just needs a real OpsUser row
too, via session_scope() - see app/ops_auth/dependencies.py).
"""
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.api.ops_auth_routes import get_my_profile, login
from app.client_auth.passwords import hash_password
from app.ops_auth.dependencies import AuthedOpsUser, get_current_ops_user, require_admin
from app.ops_auth.login_rate_limit import MAX_LOGIN_ATTEMPTS
from app.ops_auth.middleware import OpsUserAuthMiddleware
from app.ops_auth.tokens import issue_token
from app.models.ops_user import ADMIN_ROLE, VIEWER_ROLE, OpsUser
from app.schemas.ops_auth import OpsLoginBody

pytestmark = pytest.mark.integration


async def _seed_ops_user(
    db_session, *, email="ops@example.com", password="correct horse battery staple",
    is_active=True, role=ADMIN_ROLE,
):
    ops_user = OpsUser(email=email, password_hash=hash_password(password), name="Test Ops", is_active=is_active, role=role)
    db_session.add(ops_user)
    await db_session.commit()
    return ops_user


async def test_login_succeeds_with_correct_credentials_and_issues_a_usable_token(db_session, real_redis_client):
    ops_user = await _seed_ops_user(db_session)

    token = await login(OpsLoginBody(email="ops@example.com", password="correct horse battery staple"), session=db_session)

    authed = await get_current_ops_user(authorization=f"Bearer {token.access_token}", session=db_session)
    assert authed.ops_user_id == str(ops_user.id)


async def test_login_rejects_wrong_password(db_session, real_redis_client):
    await _seed_ops_user(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await login(OpsLoginBody(email="ops@example.com", password="wrong password"), session=db_session)
    assert exc_info.value.status_code == 401


async def test_login_rejects_unknown_email(db_session, real_redis_client):
    with pytest.raises(HTTPException) as exc_info:
        await login(OpsLoginBody(email="nobody@example.com", password="whatever"), session=db_session)
    assert exc_info.value.status_code == 401


async def test_login_rejects_a_deactivated_account(db_session, real_redis_client):
    await _seed_ops_user(db_session, email="deactivated@example.com", is_active=False)

    with pytest.raises(HTTPException) as exc_info:
        await login(OpsLoginBody(email="deactivated@example.com", password="correct horse battery staple"), session=db_session)
    assert exc_info.value.status_code == 401


async def test_login_is_rate_limited_after_too_many_attempts(db_session, real_redis_client):
    await _seed_ops_user(db_session, email="rate-limited@example.com")

    for _ in range(MAX_LOGIN_ATTEMPTS):
        with pytest.raises(HTTPException) as exc_info:
            await login(OpsLoginBody(email="rate-limited@example.com", password="wrong password"), session=db_session)
        assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as exc_info:
        await login(OpsLoginBody(email="rate-limited@example.com", password="wrong password"), session=db_session)
    assert exc_info.value.status_code == 429


async def test_get_my_profile_returns_the_authed_users_own_data(db_session, real_redis_client):
    ops_user = await _seed_ops_user(db_session, email="profile@example.com")
    authed = AuthedOpsUser(ops_user_id=str(ops_user.id), email=ops_user.email, name=ops_user.name, role=ops_user.role)

    profile = await get_my_profile(ops_user=authed)
    assert profile.email == "profile@example.com"
    assert profile.name == "Test Ops"
    assert profile.role == "admin"


async def test_get_current_ops_user_rejects_a_deactivated_users_token(db_session, real_redis_client):
    """Checked on every request, not just at login - revoking an ops
    user's access mid-session takes effect immediately rather than
    waiting for their token to expire on its own."""
    ops_user = await _seed_ops_user(db_session, email="revoke-me@example.com")
    token = issue_token(str(ops_user.id))

    ops_user.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await get_current_ops_user(authorization=f"Bearer {token}", session=db_session)
    assert exc_info.value.status_code == 401


async def test_get_current_ops_user_rejects_a_deleted_users_token(db_session, real_redis_client):
    fake_token = issue_token(str(uuid.uuid4()))  # no OpsUser row for this id at all

    with pytest.raises(HTTPException) as exc_info:
        await get_current_ops_user(authorization=f"Bearer {fake_token}", session=db_session)
    assert exc_info.value.status_code == 401


async def test_get_current_ops_user_rejects_missing_bearer_token(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await get_current_ops_user(authorization=None, session=db_session)
    assert exc_info.value.status_code == 401


# --- OpsUserAuthMiddleware ---------------------------------------------


async def _ok(request):
    return PlainTextResponse("ok")


def _fake_request(path: str, authorization: str | None = None) -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    headers = [(b"authorization", authorization.encode())] if authorization else []
    return Request(
        {"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": headers, "scheme": "http",
         "client": ("1.2.3.4", 1234), "server": ("testserver", 80)},
        receive,
    )


async def test_middleware_exempts_health_and_driver_client_webhook_paths():
    middleware = OpsUserAuthMiddleware(app=None)
    for path in ["/health", "/driver/me", "/client/me", "/webhooks/twilio/inbound-sms", "/ops/auth/login"]:
        response = await middleware.dispatch(_fake_request(path), _ok)
        assert response.status_code == 200


async def test_middleware_rejects_missing_bearer_token():
    middleware = OpsUserAuthMiddleware(app=None)
    response = await middleware.dispatch(_fake_request("/hubs"), _ok)
    assert response.status_code == 401


async def test_middleware_accepts_a_valid_token(db_session, real_redis_client):
    ops_user = await _seed_ops_user(db_session, email="middleware-ok@example.com")
    token = issue_token(str(ops_user.id))

    middleware = OpsUserAuthMiddleware(app=None)
    response = await middleware.dispatch(_fake_request("/hubs", authorization=f"Bearer {token}"), _ok)
    assert response.status_code == 200


async def test_middleware_rejects_a_deactivated_users_token(db_session, real_redis_client):
    ops_user = await _seed_ops_user(db_session, email="middleware-revoked@example.com")
    token = issue_token(str(ops_user.id))
    ops_user.is_active = False
    await db_session.commit()

    middleware = OpsUserAuthMiddleware(app=None)
    response = await middleware.dispatch(_fake_request("/hubs", authorization=f"Bearer {token}"), _ok)
    assert response.status_code == 401


# --- require_admin (role gating for mutating endpoints) -----------------


async def test_require_admin_allows_an_admin_user():
    admin = AuthedOpsUser(ops_user_id="u1", email="a@example.com", name="Admin", role=ADMIN_ROLE)
    result = await require_admin(ops_user=admin)
    assert result is admin


async def test_require_admin_rejects_a_viewer_user():
    viewer = AuthedOpsUser(ops_user_id="u2", email="v@example.com", name="Viewer", role=VIEWER_ROLE)
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(ops_user=viewer)
    assert exc_info.value.status_code == 403


async def test_viewer_role_round_trips_through_real_login_and_profile(db_session, real_redis_client):
    """A real, seeded viewer account - not just a hand-built AuthedOpsUser -
    logs in and sees its own role reflected correctly."""
    await _seed_ops_user(db_session, email="real-viewer@example.com", role=VIEWER_ROLE)

    token = await login(OpsLoginBody(email="real-viewer@example.com", password="correct horse battery staple"), session=db_session)
    authed = await get_current_ops_user(authorization=f"Bearer {token.access_token}", session=db_session)
    assert authed.role == VIEWER_ROLE

    profile = await get_my_profile(ops_user=authed)
    assert profile.role == "viewer"

    with pytest.raises(HTTPException) as exc_info:
        await require_admin(ops_user=authed)
    assert exc_info.value.status_code == 403
