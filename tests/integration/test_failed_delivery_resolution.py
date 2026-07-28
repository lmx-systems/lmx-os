"""
Failed-delivery / redelivery resolution (docs/ROADMAP.md R5) against real
Postgres/Redis. Calls the resolution service and the admin route function
directly, same pattern as tests/integration/test_billing.py.
"""
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.admin_routes import resolve_order
from app.batch_queue.store import HoldQueueStore
from app.billing.service import NoBillableOrdersError, generate_invoice
from app.delivery.resolution import OrderNotFailedError, resolve_failed_order
from app.models.client import Client
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.admin import ResolveFailedOrderBody

pytestmark = pytest.mark.integration


async def _seed_order(db_session, *, status=OrderStatus.delivery_failed, tier="T2", fee_cents=1_800, delivery_attempts=1):
    hub = Hub(id=uuid.uuid4(), name="R5 Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="R5 Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    shop = Shop(client_id=client.id, name="R5 Shop", address="1 Distribution Way", lat=34.06, lng=-118.24)
    db_session.add(shop)
    await db_session.commit()
    now = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
    order = Order(
        hub_id=hub.id, client_id=client.id, shop_id=shop.id,
        external_order_ref="R5-1", source_system="flat_file", raw_payload={},
        sla_tier=tier, status=status,
        failure_reason="REFUSED" if status == OrderStatus.delivery_failed else None,
        requested_at=now, updated_at=now, fee_cents=fee_cents, delivery_attempts=delivery_attempts,
    )
    db_session.add(order)
    await db_session.commit()
    return order, hub.id, client.id, shop.id


async def test_redeliver_requeues_increments_attempts_and_clears_reason(db_session, real_redis_client):
    order, hub_id, _client, _shop = await _seed_order(db_session)
    assert order.delivery_attempts == 1

    resolved = await resolve_failed_order(db_session, HoldQueueStore(), order, "redeliver")

    assert resolved.status == OrderStatus.held
    assert resolved.delivery_attempts == 2
    assert resolved.failure_reason is None  # back in flight, not failed
    assert resolved.hold_deadline is not None
    assert resolved.assigned_at is None

    # It actually re-entered the batch-hold queue, so the optimizer will pick
    # it up on its next cycle - not just a status flip.
    held = await HoldQueueStore().get_all(str(hub_id))
    assert str(order.id) in {h.order_id for h in held}


async def test_return_to_shop_is_terminal(db_session):
    order, *_ = await _seed_order(db_session)
    resolved = await resolve_failed_order(db_session, HoldQueueStore(), order, "return_to_shop")
    assert resolved.status == OrderStatus.returned


async def test_cancel_is_terminal(db_session):
    order, *_ = await _seed_order(db_session)
    resolved = await resolve_failed_order(db_session, HoldQueueStore(), order, "cancel")
    assert resolved.status == OrderStatus.cancelled


async def test_resolving_a_non_failed_order_raises(db_session):
    order, *_ = await _seed_order(db_session, status=OrderStatus.delivered)
    with pytest.raises(OrderNotFailedError):
        await resolve_failed_order(db_session, HoldQueueStore(), order, "cancel")


async def test_resolve_endpoint_rejects_unknown_action_and_missing_order(db_session):
    order, *_ = await _seed_order(db_session)
    with pytest.raises(HTTPException) as exc:
        await resolve_order(str(order.id), ResolveFailedOrderBody(action="explode"), session=db_session)
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        await resolve_order(str(uuid.uuid4()), ResolveFailedOrderBody(action="cancel"), session=db_session)
    assert exc.value.status_code == 404


async def test_resolve_endpoint_409s_for_a_non_failed_order(db_session):
    order, *_ = await _seed_order(db_session, status=OrderStatus.delivered)
    with pytest.raises(HTTPException) as exc:
        await resolve_order(str(order.id), ResolveFailedOrderBody(action="cancel"), session=db_session)
    assert exc.value.status_code == 409


async def test_a_failed_order_is_never_billed(db_session):
    # Billing keys on status=delivered, so a delivery_failed order must not
    # appear on any invoice - there's nothing billable to sweep.
    order, _hub, client_id, _shop = await _seed_order(db_session)
    with pytest.raises(NoBillableOrdersError):
        await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))


async def test_a_redelivered_then_delivered_order_bills_exactly_once(db_session):
    # A retry that eventually delivers should bill once, like any delivered
    # order - the attempt count doesn't change billing.
    order, _hub, client_id, _shop = await _seed_order(
        db_session, status=OrderStatus.delivered, fee_cents=2_500, delivery_attempts=2
    )

    invoice = await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))
    assert invoice.total_cents == 2_500

    # Re-running finds nothing new - it was billed once (Order.invoice_id set).
    with pytest.raises(NoBillableOrdersError):
        await generate_invoice(db_session, client_id, date(2026, 6, 1), date(2026, 7, 1))
