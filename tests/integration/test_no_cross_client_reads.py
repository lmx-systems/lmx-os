"""TEN-3: a cross-client read fails the build, not review.

Every route a client reaches - the portal under `/client/` and the order API
under `/api/v1/` - is called here as client A, against client B's data, on one
hub. The routes come from the OpenAPI schema, as
`test_ops_roles_gate_every_write.py` takes its writes, so a route added later is
covered without anyone remembering to add it:

  - **A route that names a resource** (`/client/orders/{order_id}`) is called
    with B's id. It must answer 403 or 404, say nothing of B, and leave B's row
    as it was. A path parameter this file cannot fill with B's resource fails the
    test until it can - that is the gate for a new kind of resource.
  - **Every other route** is called as A, and its body must not mention any of
    B's ids, references, names or addresses.

A 422 is a failure, not a pass: it means the request never reached the handler,
which proves nothing about the handler. A route whose body is required gets one
in `BODIES`.

Five per-endpoint checks existed before this (`test_client_order_search.py` and
four others), each written for its own endpoint. This is the one that cannot
fall behind the routes.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.client_auth.tokens import issue_token
from app.api.public_api_routes import _SOURCE_SYSTEM as API_SOURCE
from app.main import app
from app.models.client import Client
from app.models.client_api_key import ClientApiKey, mint_api_key
from app.models.client_user import CLIENT_ADMIN_ROLE, ClientUser
from app.models.client_webhook import ClientWebhookEndpoint, WebhookDelivery, new_webhook_secret
from app.models.hub import Hub
from app.models.invoice import Invoice
from app.models.order import Order, OrderStatus
from app.models.return_item import ReturnItem
from app.models.shop import Shop

pytestmark = pytest.mark.integration

CLIENT_PREFIXES = ("/client/", "/api/v1/")
# Signing in is how a client gets an identity; it is called without one.
NOT_SCOPED = {("post", "/client/auth/login")}

# Bodies for routes that require one, so the handler runs.
BODIES = {
    ("patch", "/client/users/{user_id}"): {"role": "member"},
    ("post", "/client/shops/{shop_id}/returns"): {"manifest": "core return"},
}


def _client_routes() -> list[tuple[str, str]]:
    return sorted(
        (method, path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(CLIENT_PREFIXES)
        for method in operations
        if (method, path) not in NOT_SCOPED
    )


def _named(routes):
    return [r for r in routes if "{" in r[1]]


def _unnamed(routes):
    return [r for r in routes if "{" not in r[1]]


async def _company(db_session, hub_id, name: str) -> dict:
    """A client with one of everything a client owns, and its ways in."""
    now = datetime.now(timezone.utc)
    client = Client(hub_id=hub_id, name=f"{name} Supply", pos_system="client_portal", signup_status="active")
    db_session.add(client)
    await db_session.flush()
    user = ClientUser(
        client_id=client.id, email=f"{name.lower()}-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="x", name=f"{name} Lead", role=CLIENT_ADMIN_ROLE,
    )
    shop = Shop(
        client_id=client.id, name=f"{name} Counter", address=f"{uuid.uuid4().int % 9000 + 100} {name} Street",
        lat=30.26, lng=-97.75, external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
    )
    db_session.add_all([user, shop])
    await db_session.flush()
    invoice = Invoice(
        client_id=client.id, period_start=(now - timedelta(days=30)).date(), period_end=now.date(),
        gross_cents=1000, credit_cents=0, total_cents=1000,
    )
    db_session.add(invoice)
    await db_session.flush()
    order = Order(
        hub_id=hub_id, client_id=client.id, shop_id=shop.id,
        external_order_ref=f"{name.upper()}-{uuid.uuid4().hex[:8]}", source_system=API_SOURCE, raw_payload={},
        source_order_ref=f"{name.upper()}-POS-{uuid.uuid4().hex[:8]}", fee_cents=1000,
        sla_tier="T2", hold_deadline=now + timedelta(hours=1), weight_units=1, status=OrderStatus.received,
        requested_at=now, delivery_address=f"{uuid.uuid4().int % 9000 + 100} {name} Avenue",
        delivery_lat=30.27, delivery_lng=-97.74, invoice_id=invoice.id,
    )
    endpoint = ClientWebhookEndpoint(
        client_id=client.id, url=f"https://{name.lower()}.example.com/hooks", secret=new_webhook_secret()
    )
    token, token_hash, prefix = mint_api_key()
    api_key = ClientApiKey(client_id=client.id, token_hash=token_hash, token_prefix=prefix)
    db_session.add_all([order, endpoint, api_key])
    await db_session.flush()
    db_session.add_all([
        WebhookDelivery(endpoint_id=endpoint.id, order_id=order.id, event_id=str(uuid.uuid4()), payload={}),
        ReturnItem(hub_id=hub_id, shop_id=shop.id, manifest=f"{name} core return"),
    ])
    await db_session.commit()
    return {
        "client": client, "user": user, "shop": shop, "invoice": invoice, "order": order,
        "endpoint": endpoint, "api_key": api_key,
        "bearer": issue_token(str(user.id), str(client.id), CLIENT_ADMIN_ROLE),
        "api_token": token,
    }


@pytest.fixture
async def companies(db_session, real_redis_client):
    hub = Hub(id=uuid.uuid4(), name="Austin Hub", lat=30.267, lng=-97.743)
    db_session.add(hub)
    await db_session.commit()
    return await _company(db_session, hub.id, "Alpha"), await _company(db_session, hub.id, "Bravo")


# Which of B's rows a path parameter names, and the value that names it.
PARAMETERS = {
    "order_id": ("order", lambda b: str(b["order"].id)),
    "invoice_id": ("invoice", lambda b: str(b["invoice"].id)),
    "shop_id": ("shop", lambda b: str(b["shop"].id)),
    "user_id": ("user", lambda b: str(b["user"].id)),
    "endpoint_id": ("endpoint", lambda b: str(b["endpoint"].id)),
    "api_key_id": ("api_key", lambda b: str(b["api_key"].id)),
    "your_order_ref": ("order", lambda b: b["order"].source_order_ref),
}


def _traces_of(b: dict) -> list[str]:
    """Everything that would identify B in a response."""
    return [
        str(b["client"].id), b["client"].name, str(b["order"].id), b["order"].external_order_ref,
        b["order"].source_order_ref,
        b["order"].delivery_address, str(b["shop"].id), b["shop"].name, b["shop"].address,
        str(b["invoice"].id), str(b["user"].id), b["user"].email, str(b["endpoint"].id), b["endpoint"].url,
        str(b["api_key"].id), b["api_key"].token_prefix,
    ]


async def _as(a: dict, method: str, path: str, body=None) -> httpx.Response:
    headers = (
        {"X-LMX-API-Key": a["api_token"]} if path.startswith("/api/v1/") else {"Authorization": f"Bearer {a['bearer']}"}
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        return await http.request(method.upper(), path, json=body, headers=headers)


def test_the_schema_still_lists_the_client_routes():
    """Guards the tests below, which would pass by checking nothing if the
    schema stopped listing these."""
    routes = _client_routes()

    assert ("get", "/client/orders/{order_id}") in routes
    assert ("get", "/api/v1/orders/{your_order_ref}") in routes
    assert len(_named(routes)) >= 12 and len(_unnamed(routes)) >= 15


@pytest.mark.parametrize(("method", "path"), _named(_client_routes()))
async def test_a_client_cannot_reach_another_clients_resource(db_session, companies, method, path):
    a, b = companies
    names = re.findall(r"\{([^}]+)\}", path)
    unknown = [n for n in names if n not in PARAMETERS]
    assert not unknown, f"{path}: say in PARAMETERS which of B's rows {unknown} names"
    concrete = path
    for name in names:
        concrete = concrete.replace(f"{{{name}}}", PARAMETERS[name][1](b))
    touched = [b[PARAMETERS[n][0]] for n in names]
    before = [(type(row), row.id, row.updated_at) for row in touched]

    response = await _as(a, method, concrete, BODIES.get((method, path)))

    assert response.status_code in (403, 404), f"{method.upper()} {concrete}: {response.status_code} {response.text}"
    leaked = [t for t in _traces_of(b) if t in response.text and t not in concrete]
    assert not leaked, f"{method.upper()} {path} told A about B: {leaked}"
    db_session.expire_all()
    for model, row_id, updated_at in before:
        row = await db_session.scalar(select(model).where(model.id == row_id))
        assert row.updated_at == updated_at, f"{method.upper()} {path} changed B's {model.__name__}"


@pytest.mark.parametrize(("method", "path"), _unnamed(_client_routes()))
async def test_no_client_route_mentions_another_client(companies, method, path):
    a, b = companies

    response = await _as(a, method, path, {} if method != "get" else None)

    leaked = [t for t in _traces_of(b) if t in response.text]
    assert not leaked, f"{method.upper()} {path} told A about B: {leaked}"
    if method == "get":
        assert response.status_code == 200, f"GET {path}: {response.status_code} {response.text}"


@pytest.mark.parametrize(("method", "path"), [r for r in _named(_client_routes()) if r[0] == "get"])
async def test_the_same_route_answers_for_its_own_client(companies, method, path):
    """The control: a 404 above means "not yours" only if the owner gets a 200."""
    a, _ = companies
    concrete = path
    for name in re.findall(r"\{([^}]+)\}", path):
        concrete = concrete.replace(f"{{{name}}}", PARAMETERS[name][1](a))

    response = await _as(a, method, concrete)

    assert response.status_code == 200, f"GET {concrete}: {response.status_code} {response.text}"
