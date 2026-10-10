"""
Failed-delivery / redelivery resolution (docs/ROADMAP.md R5) against real
Postgres/Redis. Calls the resolution service and the admin route function
directly, same pattern as tests/integration/test_billing.py.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_routes import resolve_order
from app.batch_queue.store import HoldQueueStore
from app.billing.service import NoBillableOrdersError, generate_invoice
from app.delivery.resolution import OrderNotFailedError, resolve_failed_order
from app.models.client import Client
from app.models.client_sla_term import ClientSlaTerm
from app.models.client_webhook import ClientWebhookEndpoint, WebhookDelivery, new_webhook_secret
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.rules import ActiveRule
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
        delivered_at=now if status == OrderStatus.delivered else None,
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


async def test_a_redelivery_is_held_by_the_shops_own_rule(db_session, real_redis_client):
    """An approved "hold this shop's orders 30 minutes" rule applies to an order
    coming back for a second attempt as it did to the first. Redelivery used the
    default 90 alone."""
    order, hub_id, _client, shop_id = await _seed_order(db_session)
    db_session.add(
        ActiveRule(
            hub_id=hub_id,
            rule_type="sla_hold_window_override",
            scope={"shop_id": str(shop_id)},
            value={"T2": 30},
        )
    )
    await db_session.commit()

    before = datetime.now(timezone.utc)
    resolved = await resolve_failed_order(db_session, HoldQueueStore(), order, "redeliver")

    held_for = resolved.hold_deadline - before
    assert timedelta(minutes=29) < held_for < timedelta(minutes=31)


async def test_a_redelivery_already_past_its_promise_is_not_held(db_session, real_redis_client):
    """The first attempt failed and the promise has gone by. Holding it another 90
    minutes for a batching partner would make a late order later; it goes now."""
    order, hub_id, client_id, _shop = await _seed_order(db_session)
    db_session.add(ClientSlaTerm(client_id=client_id, sla_tier="T2", delivery_target_minutes=60))
    await db_session.commit()

    resolved = await resolve_failed_order(db_session, HoldQueueStore(), order, "redeliver")

    assert resolved.hold_deadline <= datetime.now(timezone.utc) + timedelta(seconds=5)


async def test_an_external_redelivery_is_held_against_its_window_not_our_terms(
    db_session, real_redis_client
):
    """Somebody else promised this customer a window. Intake holds against it and
    looks up no contract term of ours; a redelivery does the same, rather than
    collapsing the hold by a target that was never promised."""
    order, hub_id, client_id, _shop = await _seed_order(db_session)
    window = datetime.now(timezone.utc) + timedelta(hours=3)
    order.sla_owner = "EXTERNAL"
    order.delivery_window_end = window
    # A term that, wrongly applied from the June request time, would release at once.
    db_session.add(ClientSlaTerm(client_id=client_id, sla_tier="T2", delivery_target_minutes=60))
    await db_session.commit()

    resolved = await resolve_failed_order(db_session, HoldQueueStore(), order, "redeliver")

    # Held until the window less the drive and the buffer, as intake would hold it:
    # well after now, and before the window itself.
    assert datetime.now(timezone.utc) + timedelta(hours=2) < resolved.hold_deadline < window


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


@pytest.mark.parametrize(
    ("action", "told"),
    [
        ("redeliver", ("EXCEPTION_RAISED", "HELD")),
        ("return_to_shop", ("EXCEPTION_RAISED", "RETURNED_TO_HUB")),
        ("cancel", ("EXCEPTION_RAISED", "CANCELLED")),
    ],
)
async def test_the_client_hears_how_a_failed_order_was_resolved(
    db_session, real_redis_client, action, told
):
    """Each resolution wrote its status directly, around the state machine, so a
    client's webhook last heard that the delivery failed and never what became
    of the order."""
    order, _hub, client_id, _shop = await _seed_order(db_session)
    db_session.add(
        ClientWebhookEndpoint(
            client_id=client_id,
            url="https://consumer.example.com/lmx",
            secret=new_webhook_secret(),
        )
    )
    await db_session.commit()

    await resolve_failed_order(db_session, HoldQueueStore(), order, action)

    sent = (
        await db_session.execute(select(WebhookDelivery).order_by(WebhookDelivery.sequence))
    ).scalars()
    assert [(row.payload["previous_status"], row.payload["status"]) for row in sent] == [told]
