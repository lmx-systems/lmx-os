"""Every write an ops session can make is gated by tier, and the tiers hold.

Three roles (app/models/ops_user.py): admins configure, dispatchers do the day's
order work, viewers read. `OpsUserAuthMiddleware` checks only that a session is
valid, so each write carries `require_admin` or `require_dispatcher` itself,
and a write carrying neither is open to a viewer. The writes come from the
OpenAPI schema rather than a list kept here, so a write added without a guard
fails this too. The one list kept here is which writes are the dispatcher's.
"""
import re
import uuid

import httpx
import pytest

from app.client_auth.passwords import hash_password
from app.main import app
from app.models.ops_user import ADMIN_ROLE, DISPATCHER_ROLE, VIEWER_ROLE, OpsUser
from app.ops_auth.middleware import _is_exempt
from app.ops_auth.tokens import issue_token

pytestmark = pytest.mark.integration

WRITE_METHODS = ("post", "put", "patch", "delete")

# The day's order work. Everything else an ops session can write configures the
# hub and stays with admins.
DISPATCHER_WRITES = {
    ("post", "/orders/{order_id}/override"),
    ("post", "/orders/{order_id}/consequence"),
    ("post", "/operations/docks/{location_id}/node-class"),
    ("post", "/operations/linkage-flags/{flag_id}/resolve"),
    ("post", "/optimizer/{hub_id}/run-cycle"),
    ("post", "/ingestion/{hub_id}/{client_id}/{source_system}"),
    ("post", "/admin/orders/{order_id}/cancel"),
    ("post", "/admin/orders/{order_id}/resolve"),
    ("post", "/admin/returns/{return_id}/mark-returned"),
    ("post", "/admin/returns/{return_id}/reschedule"),
}

# Reads a dispatcher's screens load, which were admin-only before the tier existed.
DISPATCHER_READS = {
    ("get", "/admin/hubs/{hub_id}/returns"),
    ("get", "/admin/hubs/{hub_id}/cod-disputes"),
    ("get", "/admin/dock-log/submissions"),
}


def _ops_writes() -> list[tuple[str, str]]:
    return sorted(
        (method, path)
        for path, operations in app.openapi()["paths"].items()
        if not _is_exempt(path)
        for method in operations
        if method in WRITE_METHODS
    )


async def _token_for(db_session, role: str) -> str:
    user = OpsUser(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("correct horse battery staple"),
        name=f"Test {role}",
        is_active=True,
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    return issue_token(str(user.id))


async def _send(method: str, path: str, token: str) -> httpx.Response:
    # Any id will do: the role is checked before anything is looked up, so a
    # refusal is a 403 and a pass is whatever the handler says about a stranger
    # (404, 409 or 422), never a 403.
    concrete = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(
            method.upper(), concrete, json={}, headers={"Authorization": f"Bearer {token}"}
        )


def test_the_schema_still_lists_the_writes():
    """Guards the tests below, which would pass by checking nothing if the
    schema stopped listing these."""
    writes = _ops_writes()

    assert DISPATCHER_WRITES <= set(writes), DISPATCHER_WRITES - set(writes)
    assert ("put", "/admin/clients/{client_id}/rates") in writes
    assert len(writes) >= 37


@pytest.mark.parametrize(("method", "path"), _ops_writes())
async def test_a_viewer_cannot_write(db_session, real_redis_client, method, path):
    token = await _token_for(db_session, VIEWER_ROLE)

    response = await _send(method, path, token)

    assert response.status_code == 403, response.text


@pytest.mark.parametrize(("method", "path"), _ops_writes())
async def test_a_dispatcher_gets_the_days_writes_and_no_others(db_session, real_redis_client, method, path):
    token = await _token_for(db_session, DISPATCHER_ROLE)

    response = await _send(method, path, token)

    if (method, path) in DISPATCHER_WRITES:
        assert response.status_code != 403, response.text
    else:
        assert response.status_code == 403, response.text


@pytest.mark.parametrize(("method", "path"), sorted(DISPATCHER_WRITES | DISPATCHER_READS))
async def test_an_admin_is_never_refused(db_session, real_redis_client, method, path):
    token = await _token_for(db_session, ADMIN_ROLE)

    response = await _send(method, path, token)

    assert response.status_code != 403, response.text


@pytest.mark.parametrize(("method", "path"), sorted(DISPATCHER_READS))
async def test_a_dispatcher_can_load_the_screens_behind_their_writes(db_session, real_redis_client, method, path):
    """A returns list a dispatcher can't read makes a return they can't close."""
    token = await _token_for(db_session, DISPATCHER_ROLE)

    response = await _send(method, path, token)

    assert response.status_code != 403, response.text
