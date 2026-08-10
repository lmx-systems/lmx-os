"""
Outbound status webhooks (docs/ROADMAP.md F4) against real Postgres + Redis.

§1.4: *"A carrier that takes orders and goes quiet is not a carrier - it is a
favour."* A webhook that POSTs once and drops the event because the consumer
happened to be restarting is that same silence with extra steps, so the tests that
matter most are the ones about what survives failure.

Three properties this file exists to pin down:

1. **The notification commits with the fact.** `emit_status_change` runs inside
   `advance_orders`, BEFORE the caller commits - so an inline POST could tell a
   client an order was delivered on a transaction that then rolled back. There is
   no un-sending that.
2. **What is retried and what is not.** A 500 gets another go; a 400 does not, and
   must not count against the endpoint's failure budget.
3. **A client-supplied URL is an SSRF primitive**, not a config field.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException

from app.api.client_routes import (
    create_my_webhook,
    disable_my_webhook,
    enable_my_webhook,
    list_my_webhook_deliveries,
    list_my_webhooks,
)
from app.client_auth.dependencies import AuthedClient
from app.models.client import Client
from app.models.client_webhook import (
    DELIVERY_DELIVERED,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_REJECTED,
    MAX_CONSECUTIVE_FAILURES,
    ClientWebhookEndpoint,
    WebhookDelivery,
    new_webhook_secret,
)
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.orders.status_service import advance_orders
from app.schemas.webhooks import WebhookEndpointBody
from app.webhooks import delivery as delivery_module
from app.webhooks.delivery import MAX_ATTEMPTS, attempt_delivery, deliver_pending
from app.webhooks.signing import sign, verify
from app.webhooks import url_safety
from app.webhooks.url_safety import UnsafeWebhookUrl, validate_webhook_url

pytestmark = pytest.mark.integration


def _authed(client_id) -> AuthedClient:
    return AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="ops@distributor.test",
        name="Dana",
        role="admin",
    )


async def _seed(db_session):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file")
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address="220 Harbor St",
            lat=30.26,
            lng=-97.74,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()
    return hub_id, client_id, shop_id


async def _endpoint(db_session, client_id, **overrides) -> ClientWebhookEndpoint:
    endpoint = ClientWebhookEndpoint(
        client_id=client_id,
        url="https://consumer.example.com/lmx",
        secret=new_webhook_secret(),
        **overrides,
    )
    db_session.add(endpoint)
    await db_session.commit()
    return endpoint


async def _order(
    db_session, hub_id, client_id, shop_id, status=OrderStatus.queued, source_order_ref=None
) -> Order:
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_order_ref=source_order_ref or f"EPICOR-{uuid.uuid4().hex[:10]}",
        source_system="epicor",
        raw_payload={},
        sla_tier="T2",
        hold_deadline=now + timedelta(minutes=30),
        weight_units=1,
        status=status,
        requested_at=now,
    )
    db_session.add(order)
    await db_session.commit()
    return order


async def _deliveries(db_session, endpoint_id=None) -> list[WebhookDelivery]:
    from sqlalchemy import select

    stmt = select(WebhookDelivery).order_by(WebhookDelivery.sequence)
    if endpoint_id is not None:
        stmt = stmt.where(WebhookDelivery.endpoint_id == endpoint_id)
    return list((await db_session.execute(stmt)).scalars().all())


def _consumer(handler):
    """A fake consumer for the delivery module's `_new_client` seam.

    Patching `delivery_module.httpx.AsyncClient` instead would patch the httpx
    module itself, so the replacement's own `httpx.AsyncClient(...)` call recurses
    into the patch - a RecursionError reported as a delivery failure.
    """
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _dns(monkeypatch):
    """Resolve any hostname to a public address.

    URL safety fails closed on a lookup failure, which is right in production and
    would otherwise make every test here depend on `consumer.example.com` existing.
    The private-address path is tested explicitly below by stubbing this to one.
    """
    monkeypatch.setattr(url_safety, "_resolve", lambda hostname, port: {"93.184.216.34"})


# ---------------------------------------------------------------------------
# The transactional enqueue
# ---------------------------------------------------------------------------


async def test_a_status_change_enqueues_one_delivery_per_active_endpoint(
    db_session, real_redis_client
):
    hub_id, client_id, shop_id = await _seed(db_session)
    first = await _endpoint(db_session, client_id, description="warehouse")
    second = await _endpoint(db_session, client_id, description="slack relay")
    await _endpoint(db_session, client_id, is_active=False, description="old server")
    order = await _order(db_session, hub_id, client_id, shop_id)

    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()

    rows = await _deliveries(db_session)
    assert {row.endpoint_id for row in rows} == {first.id, second.id}
    # One event id shared across a client's endpoints, so they can correlate two
    # integrations receiving the same transition.
    assert len({row.event_id for row in rows}) == 1


async def test_a_rolled_back_transition_notifies_nobody(db_session, real_redis_client):
    """**The reason the sink enqueues instead of POSTing.** An inline send could
    tell a client an order was delivered on a transaction that then rolled back, and
    there is no way to un-send that."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)

    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.rollback()

    assert await _deliveries(db_session) == []


async def test_a_skipped_illegal_transition_enqueues_nothing(db_session, real_redis_client):
    """advance_orders skips transitions the state machine forbids; a webhook for a
    status change that never happened would be worse than none."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id, status=OrderStatus.delivered)

    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()

    assert await _deliveries(db_session) == []


async def test_a_client_with_no_endpoints_costs_nothing(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)

    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()

    assert await _deliveries(db_session) == []


async def test_the_payload_speaks_the_public_vocabulary(db_session, real_redis_client):
    """`classified` and `queued` are our business. A consumer gets the §1.4 label
    and the ref their own system recognises."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(
        db_session, hub_id, client_id, shop_id, source_order_ref="EPICOR-99812"
    )

    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()

    payload = (await _deliveries(db_session))[0].payload
    assert payload["type"] == "order.status_changed"
    assert payload["status"] == "ASSIGNED"
    assert payload["source_order_ref"] == "EPICOR-99812"
    assert payload["order_id"] == str(order.id)
    # Nothing internal leaks: no client id, no hub id, no shop id.
    rendered = json.dumps(payload)
    for internal in (str(client_id), str(hub_id), str(shop_id)):
        assert internal not in rendered


# ---------------------------------------------------------------------------
# Delivery, and what a failure means
# ---------------------------------------------------------------------------


async def test_a_2xx_marks_the_delivery_done(db_session, real_redis_client, monkeypatch):
    hub_id, client_id, shop_id = await _seed(db_session)
    endpoint = await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(200))
    )
    outcome = await attempt_delivery(db_session, row)

    assert outcome == DELIVERY_DELIVERED
    assert row.delivered_at is not None
    assert row.next_attempt_at is None
    await db_session.commit()
    await db_session.refresh(endpoint)
    assert endpoint.last_success_at is not None


async def test_the_request_is_signed_so_the_consumer_can_verify_it(
    db_session, real_redis_client, monkeypatch
):
    hub_id, client_id, shop_id = await _seed(db_session)
    endpoint = await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["headers"] = dict(request.headers)
        return httpx.Response(200)

    monkeypatch.setattr(delivery_module, "_new_client", _consumer(handler))
    await attempt_delivery(db_session, row)

    assert verify(
        endpoint.secret,
        seen["body"],
        seen["headers"]["x-lmx-signature"],
        now=int(datetime.now(timezone.utc).timestamp()),
    )
    assert seen["headers"]["x-lmx-event-id"] == row.event_id
    assert seen["headers"]["x-lmx-delivery-attempt"] == "1"


async def test_a_5xx_is_retried_with_backoff(db_session, real_redis_client, monkeypatch):
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(503))
    )
    outcome = await attempt_delivery(db_session, row)

    assert outcome == DELIVERY_PENDING
    assert row.attempts == 1
    assert row.next_attempt_at > datetime.now(timezone.utc)
    assert row.last_status_code == 503


async def test_a_4xx_is_not_retried(db_session, real_redis_client, monkeypatch):
    """**The distinction that matters.** The consumer said it doesn't want this;
    retrying a 400 six times spends our budget to be told the same thing."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(400))
    )
    outcome = await attempt_delivery(db_session, row)

    assert outcome == DELIVERY_REJECTED
    assert row.next_attempt_at is None


async def test_a_rejection_does_not_count_against_the_endpoint(
    db_session, real_redis_client, monkeypatch
):
    """A handler that 422s an event type they haven't implemented must not lose them
    the ones they have - `consecutive_failures` disables the endpoint."""
    hub_id, client_id, shop_id = await _seed(db_session)
    endpoint = await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(422))
    )
    await attempt_delivery(db_session, row)

    await db_session.commit()
    await db_session.refresh(endpoint)
    assert endpoint.consecutive_failures == 0
    assert endpoint.is_active is True


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503])
async def test_transient_statuses_are_retryable(
    db_session, real_redis_client, monkeypatch, status_code
):
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    monkeypatch.setattr(
        delivery_module,
        "_new_client",
        _consumer(lambda r: httpx.Response(status_code)),
    )
    assert await attempt_delivery(db_session, row) == DELIVERY_PENDING


async def test_a_connection_failure_is_retried(db_session, real_redis_client, monkeypatch):
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    def explode(request):
        raise httpx.ConnectTimeout("no route to host")

    monkeypatch.setattr(delivery_module, "_new_client", _consumer(explode))
    assert await attempt_delivery(db_session, row) == DELIVERY_PENDING
    assert "ConnectTimeout" in row.last_error


async def test_retries_are_eventually_given_up_on(db_session, real_redis_client, monkeypatch):
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(503))
    )
    for _ in range(MAX_ATTEMPTS):
        outcome = await attempt_delivery(db_session, row)

    assert outcome == DELIVERY_FAILED
    assert row.next_attempt_at is None
    assert row.attempts == MAX_ATTEMPTS


async def test_a_persistently_dead_endpoint_is_switched_off(
    db_session, real_redis_client, monkeypatch
):
    """An endpoint dead this long isn't coming back on its own, and retrying it
    forever spends our budget on a client who decommissioned a server without
    telling us. Switched off rather than deleted, so they can see it in their
    portal."""
    hub_id, client_id, shop_id = await _seed(db_session)
    endpoint = await _endpoint(db_session, client_id)
    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(503))
    )

    for _ in range(MAX_CONSECUTIVE_FAILURES):
        order = await _order(db_session, hub_id, client_id, shop_id)
        await advance_orders(db_session, [order.id], OrderStatus.assigned)
        await db_session.commit()
        row = (await _deliveries(db_session, endpoint.id))[-1]
        await attempt_delivery(db_session, row)
        await db_session.commit()

    await db_session.commit()
    await db_session.refresh(endpoint)
    assert endpoint.is_active is False
    assert endpoint.disabled_at is not None


async def test_a_success_clears_the_failure_count(db_session, real_redis_client, monkeypatch):
    hub_id, client_id, shop_id = await _seed(db_session)
    endpoint = await _endpoint(db_session, client_id, consecutive_failures=5)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(204))
    )
    await attempt_delivery(db_session, row)

    await db_session.commit()
    await db_session.refresh(endpoint)
    assert endpoint.consecutive_failures == 0


async def test_an_endpoint_disabled_after_enqueue_is_not_delivered_to(
    db_session, real_redis_client, monkeypatch
):
    """Retrying it would resurrect traffic to an endpoint we switched off."""
    hub_id, client_id, shop_id = await _seed(db_session)
    endpoint = await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]

    endpoint.is_active = False
    await db_session.commit()

    called = []
    monkeypatch.setattr(
        delivery_module,
        "_new_client",
        _consumer(lambda r: called.append(1) or httpx.Response(200)),
    )
    assert await attempt_delivery(db_session, row) == DELIVERY_REJECTED
    assert called == []


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


async def test_the_sweep_delivers_what_is_due(db_session, real_redis_client, monkeypatch):
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    for _ in range(3):
        order = await _order(db_session, hub_id, client_id, shop_id)
        await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(200))
    )
    counts = await deliver_pending(db_session)

    assert counts == {DELIVERY_DELIVERED: 3}


async def test_the_sweep_skips_what_is_not_due_yet(db_session, real_redis_client, monkeypatch):
    """Backoff means a just-failed delivery must not be retried by the very next
    sweep."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()
    row = (await _deliveries(db_session))[0]
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await db_session.commit()

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(200))
    )
    assert await deliver_pending(db_session) == {}


async def test_the_sweep_does_not_re_deliver_a_finished_delivery(
    db_session, real_redis_client, monkeypatch
):
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(200))
    )
    await deliver_pending(db_session)
    assert await deliver_pending(db_session) == {}


async def test_the_sweep_delivers_in_event_order(db_session, real_redis_client, monkeypatch):
    """Retries make arrival order unreliable in general - which is why `sequence` is
    in the payload - but there's no reason to make it worse than it has to be."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    # A real path through the machine - assigned -> accepted is not legal, and a
    # skipped transition would silently shorten this list.
    for status in (
        OrderStatus.assigned,
        OrderStatus.en_route_pickup,
        OrderStatus.picked_up,
    ):
        await advance_orders(db_session, [order.id], status)
        await db_session.commit()

    received: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content)["status"])
        return httpx.Response(200)

    monkeypatch.setattr(delivery_module, "_new_client", _consumer(handler))
    await deliver_pending(db_session)

    assert received == ["ASSIGNED", "EN_ROUTE_PICKUP", "PICKED_UP"]


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def test_a_signature_verifies_against_the_body_and_the_clock():
    secret, body = "s3cret", b'{"a":1}'
    now = 1_754_841_600
    header = sign(secret, body, now)

    assert verify(secret, body, header, now=now)
    assert not verify("wrong-secret", body, header, now=now)
    assert not verify(secret, b'{"a":2}', header, now=now)


def test_an_old_signature_is_rejected_even_though_the_digest_is_right():
    """**Why the timestamp is inside the signed string.** Signing the body alone
    produces a token that never expires, so anyone who captures one request can
    replay it forever - and a replayed "delivered" is a consumer marking an order
    complete that isn't."""
    secret, body = "s3cret", b'{"a":1}'
    signed_at = 1_754_841_600
    header = sign(secret, body, signed_at)

    assert verify(secret, body, header, now=signed_at + 60)
    assert not verify(secret, body, header, now=signed_at + 3600)


def test_a_malformed_signature_header_is_rejected_not_crashed():
    for header in ("", "garbage", "t=notanumber,v1=abc", "v1=abc", "t=123"):
        assert not verify("s", b"{}", header, now=123)


# ---------------------------------------------------------------------------
# SSRF
# ---------------------------------------------------------------------------


def test_a_public_https_url_is_accepted():
    assert validate_webhook_url("https://consumer.example.com/hooks/lmx")


@pytest.mark.parametrize(
    "url",
    [
        "http://consumer.example.com/hooks",  # plaintext leaks order data on the wire
        "https://localhost/hooks",
        "https://127.0.0.1/hooks",
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata
        "https://10.0.0.5/hooks",
        "https://192.168.1.10/hooks",
        "https://[::1]/hooks",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://user:pass@consumer.example.com/hooks",
        "ftp://consumer.example.com/hooks",
        "not a url",
    ],
)
def test_unsafe_urls_are_refused(url):
    """**A webhook endpoint is an SSRF primitive.** Our backend makes repeated
    outbound requests to whatever is stored here, from inside the network - so
    without this a client can aim it at the cloud metadata service or at our own
    internal router."""
    with pytest.raises(UnsafeWebhookUrl):
        validate_webhook_url(url)


# ---------------------------------------------------------------------------
# The client's own configuration surface
# ---------------------------------------------------------------------------


async def test_creating_an_endpoint_returns_the_secret_exactly_once(
    db_session, real_redis_client
):
    """The same shape as any API key. Returning it on every list would put a live
    signing key in a response, a browser cache and an access log on every page
    load."""
    _hub_id, client_id, _shop_id = await _seed(db_session)
    authed = _authed(client_id)

    created = await create_my_webhook(
        WebhookEndpointBody(url="https://consumer.example.com/lmx", description="warehouse"),
        client=authed,
        session=db_session,
    )
    assert created.secret

    listed = await list_my_webhooks(client=authed, session=db_session)
    assert len(listed) == 1
    assert not hasattr(listed[0], "secret")


async def test_an_unsafe_url_is_refused_at_the_endpoint(db_session, real_redis_client):
    _hub_id, client_id, _shop_id = await _seed(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await create_my_webhook(
            WebhookEndpointBody(url="http://169.254.169.254/"),
            client=_authed(client_id),
            session=db_session,
        )
    assert exc_info.value.status_code == 422


async def test_a_client_cannot_see_or_touch_another_clients_endpoint(
    db_session, real_redis_client
):
    hub_id, mine, _shop_id = await _seed(db_session)
    theirs = uuid.uuid4()
    db_session.add(Client(id=theirs, hub_id=hub_id, name="Other Co", pos_system="flat_file"))
    await db_session.commit()
    their_endpoint = await _endpoint(db_session, theirs)

    assert await list_my_webhooks(client=_authed(mine), session=db_session) == []
    # A 404 rather than a 403, so this isn't an existence oracle for other clients'
    # integration ids.
    with pytest.raises(HTTPException) as exc_info:
        await disable_my_webhook(
            str(their_endpoint.id), client=_authed(mine), session=db_session
        )
    assert exc_info.value.status_code == 404


async def test_re_enabling_clears_the_failure_count(db_session, real_redis_client):
    """Otherwise an endpoint at the cutoff is switched off again by its very next
    failure and the client never gets a clean run at proving their fix worked."""
    _hub_id, client_id, _shop_id = await _seed(db_session)
    endpoint = await _endpoint(
        db_session,
        client_id,
        is_active=False,
        consecutive_failures=MAX_CONSECUTIVE_FAILURES,
        disabled_at=datetime.now(timezone.utc),
    )

    view = await enable_my_webhook(
        str(endpoint.id), client=_authed(client_id), session=db_session
    )

    assert view.is_active is True
    assert view.consecutive_failures == 0
    assert view.disabled_at is None


async def test_a_disabled_endpoint_stops_receiving_events(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed(db_session)
    endpoint = await _endpoint(db_session, client_id)
    authed = _authed(client_id)

    await disable_my_webhook(str(endpoint.id), client=authed, session=db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()

    assert await _deliveries(db_session) == []


async def test_the_client_can_see_why_their_handler_is_failing(
    db_session, real_redis_client, monkeypatch
):
    """"Did you actually send it?" is the first question of every webhook
    integration, and without this the honest answer is "check our logs"."""
    hub_id, client_id, shop_id = await _seed(db_session)
    endpoint = await _endpoint(db_session, client_id)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await advance_orders(db_session, [order.id], OrderStatus.assigned)
    await db_session.commit()

    monkeypatch.setattr(
        delivery_module, "_new_client", _consumer(lambda r: httpx.Response(500))
    )
    await deliver_pending(db_session)

    history = await list_my_webhook_deliveries(
        str(endpoint.id), client=_authed(client_id), session=db_session
    )
    assert len(history) == 1
    assert history[0].status == DELIVERY_PENDING
    assert history[0].last_status_code == 500
    assert history[0].attempts == 1
    assert history[0].next_attempt_at is not None


def test_a_hostname_that_resolves_privately_is_refused(monkeypatch):
    """**The case the literal-IP checks miss.** `internal-api.acme.com` looks like an
    ordinary public hostname and resolves to 10.x - which is exactly how an
    attacker gets past a scheme-and-shape check."""
    monkeypatch.setattr(url_safety, "_resolve", lambda hostname, port: {"10.1.2.3"})

    with pytest.raises(UnsafeWebhookUrl):
        validate_webhook_url("https://internal-api.acme.com/hooks")


def test_a_hostname_resolving_to_both_public_and_private_is_refused(monkeypatch):
    """Checking only the first address would pass on one lookup and reach the
    private one on the next."""
    monkeypatch.setattr(
        url_safety, "_resolve", lambda hostname, port: {"93.184.216.34", "192.168.0.9"}
    )

    with pytest.raises(UnsafeWebhookUrl):
        validate_webhook_url("https://mixed.acme.com/hooks")


def test_a_hostname_that_does_not_resolve_is_refused(monkeypatch):
    """Fails closed. We cannot tell a public host from a private one without a
    lookup, and guessing permissively is how `internal-api` gets stored during a DNS
    blip."""
    import socket

    def boom(hostname, port):
        raise socket.gaierror("nope")

    monkeypatch.setattr(url_safety, "_resolve", boom)

    with pytest.raises(UnsafeWebhookUrl):
        validate_webhook_url("https://nonexistent.invalid/hooks")
