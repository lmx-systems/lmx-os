"""A client can take an order back before it is collected.

The only cancel anywhere was ops resolving a failed delivery, so an order a
client placed twice, or for a part the shop then found on its own shelf, went
out regardless and was billed. Before a driver has it, withdrawing an order
costs nothing; after, it is a stop on a route somebody is driving, and that is
dispatch's call.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.client_routes import cancel_my_order
from app.api.public_api_routes import cancel_order, submit_order
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.models.client import Client
from app.models.client_webhook import ClientWebhookEndpoint, WebhookDelivery, new_webhook_secret
from app.models.order import Order, OrderStatus
from tests.integration import test_client_portal_integration as portal
from tests.integration import test_public_order_api as api

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _geocoder(monkeypatch):
    """The public API geocodes a pickup address itself; its own tests' fake
    doesn't travel with an import, so it is installed here too."""
    import app.api.public_api_routes as routes

    monkeypatch.setattr(routes, "get_geocoder", lambda: api._FakeGeocoder())


async def _order(db_session, client_id, shop_id, *, status=OrderStatus.held) -> Order:
    hub_id = (await db_session.get(Client, client_id)).hub_id
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}", source_system="client_portal", raw_payload={},
        sla_tier="T2", status=status, requested_at=now, hold_deadline=now + timedelta(hours=1),
        delivery_address="500 Client St", delivery_lat=30.27, delivery_lng=-97.74,
        assigned_at=now if status == OrderStatus.assigned else None,
        delivered_at=now if status == OrderStatus.delivered else None,
    )
    db_session.add(order)
    await db_session.commit()
    if status in (OrderStatus.held, OrderStatus.queued):
        await HoldQueueStore().add(
            str(hub_id),
            HeldOrder(
                order_id=str(order.id), shop_lat=30.26, shop_lng=-97.73, sla_tier="T2",
                hold_deadline=order.hold_deadline, held_since=now,
            ),
        )
    return order


async def _events(db_session):
    rows = (await db_session.execute(select(WebhookDelivery).order_by(WebhookDelivery.sequence))).scalars()
    return [(row.payload["previous_status"], row.payload["status"]) for row in rows]


async def test_a_held_order_is_cancelled_and_leaves_the_queue(db_session, real_redis_client):
    authed, client_id, shop_id = await portal._onboard_and_authed(db_session, "cancel@example.com")
    db_session.add(ClientWebhookEndpoint(client_id=client_id, url="https://consumer.example.com/lmx", secret=new_webhook_secret()))
    await db_session.commit()
    order = await _order(db_session, client_id, shop_id)

    view = await cancel_my_order(str(order.id), client=authed, session=db_session)

    assert view.status == "cancelled"
    assert await HoldQueueStore().get_all(str(order.hub_id)) == []
    assert await _events(db_session) == [("HELD", "CANCELLED")]


async def test_an_order_a_driver_has_cannot_be_cancelled_from_the_portal(db_session, real_redis_client):
    authed, client_id, shop_id = await portal._onboard_and_authed(db_session, "cancel-assigned@example.com")
    order = await _order(db_session, client_id, shop_id, status=OrderStatus.assigned)

    with pytest.raises(HTTPException) as refused:
        await cancel_my_order(str(order.id), client=authed, session=db_session)

    assert refused.value.status_code == 409
    assert "Call dispatch" in refused.value.detail
    order_id = order.id
    db_session.expire_all()
    assert (await db_session.get(Order, order_id)).status == OrderStatus.assigned


async def test_a_delivered_order_cannot_be_cancelled(db_session, real_redis_client):
    authed, client_id, shop_id = await portal._onboard_and_authed(db_session, "cancel-delivered@example.com")
    order = await _order(db_session, client_id, shop_id, status=OrderStatus.delivered)

    with pytest.raises(HTTPException) as refused:
        await cancel_my_order(str(order.id), client=authed, session=db_session)
    assert refused.value.status_code == 409


async def test_cancelling_twice_is_the_same_answer(db_session, real_redis_client):
    """A lost response is retried; the retry must not be a 409."""
    authed, client_id, shop_id = await portal._onboard_and_authed(db_session, "cancel-twice@example.com")
    order = await _order(db_session, client_id, shop_id)

    first = await cancel_my_order(str(order.id), client=authed, session=db_session)
    second = await cancel_my_order(str(order.id), client=authed, session=db_session)

    assert (first.status, second.status) == ("cancelled", "cancelled")


async def test_another_clients_order_is_not_found(db_session, real_redis_client):
    authed_a, _, _ = await portal._onboard_and_authed(db_session, "cancel-a@example.com")
    _, client_b, shop_b = await portal._onboard_and_authed(db_session, "cancel-b@example.com")
    order = await _order(db_session, client_b, shop_b)

    with pytest.raises(HTTPException) as refused:
        await cancel_my_order(str(order.id), client=authed_a, session=db_session)
    assert refused.value.status_code == 404


async def test_an_integrator_cancels_by_their_own_reference(db_session, real_redis_client):
    _hub_id, client_id = await api._seed_client(db_session)
    token = await api._key_for(db_session, client_id)
    body = api._order_body(your_order_ref="POS-CANCEL-1")
    await submit_order(body, api_client=await api._authed(db_session, token), session=db_session)

    result = await cancel_order("POS-CANCEL-1", api_client=await api._authed(db_session, token), session=db_session)

    assert result.status == "cancelled"
    assert result.your_order_ref == "POS-CANCEL-1"


async def test_an_integrator_cannot_cancel_another_clients_order(db_session, real_redis_client):
    _hub_a, client_a = await api._seed_client(db_session)
    _hub_b, client_b = await api._seed_client(db_session)
    token_a, token_b = await api._key_for(db_session, client_a), await api._key_for(db_session, client_b)
    await submit_order(
        api._order_body(your_order_ref="POS-SHARED-REF"),
        api_client=await api._authed(db_session, token_a),
        session=db_session,
    )

    with pytest.raises(HTTPException) as refused:
        await cancel_order("POS-SHARED-REF", api_client=await api._authed(db_session, token_b), session=db_session)
    assert refused.value.status_code == 404
