"""
Failed-delivery resolution (docs/ROADMAP.md R5).

A driver flagging a stop (app/api/driver_routes.py's flag_stop_issue) sets
the covered order(s) to OrderStatus.delivery_failed - and before this,
they'd sit there with no defined next step. This module is that next step,
taken by ops (app/api/admin_routes.py's resolve endpoint):

  - redeliver       reattempt the delivery (re-enters the dispatch pipeline)
  - return_to_shop  send the parts back to the originating shop (terminal)
  - cancel          give up on the order (terminal)

Billing correctness falls out for free: app/billing/service.py only ever
bills orders in status `delivered`, so a failed/returned/cancelled order is
never billed, and a redelivered order bills exactly once - when (if) the
retry actually delivers. No billing-side change is needed for R5.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.sla.engine import resolve_hold_window_minutes

REDELIVER = "redeliver"
RETURN_TO_SHOP = "return_to_shop"
CANCEL = "cancel"
RESOLUTION_ACTIONS = (REDELIVER, RETURN_TO_SHOP, CANCEL)


class OrderNotFailedError(Exception):
    """Only an order currently in delivery_failed can be resolved - a
    delivered/cancelled/in-flight order has no failure to resolve."""


async def resolve_failed_order(
    session: AsyncSession, hold_queue: HoldQueueStore, order: Order, action: str
) -> Order:
    if order.status != OrderStatus.delivery_failed:
        raise OrderNotFailedError(
            f"Order {order.id} is '{order.status.value}', not 'delivery_failed' - nothing to resolve"
        )
    if action == REDELIVER:
        await _redeliver(session, hold_queue, order)
    elif action == RETURN_TO_SHOP:
        order.status = OrderStatus.returned
    elif action == CANCEL:
        order.status = OrderStatus.cancelled
    else:
        raise ValueError(f"Unknown resolution action: {action!r}")
    await session.commit()
    return order


async def _redeliver(session: AsyncSession, hold_queue: HoldQueueStore, order: Order) -> None:
    """Put a failed order back into the dispatch pipeline for another
    attempt. It re-enters through the batch-hold queue exactly like a
    freshly ingested order, so the optimizer builds a new pickup+dropoff
    pair on its next cycle; the old failed stop stays as history.

    v1 assumption: the parts are re-picked from the originating shop, so the
    new pickup clusters at the shop's location like the first attempt did.
    Modeling parts that are physically elsewhere by then (still on the van,
    already back at the hub) is the deeper returns/cores question tracked as
    W1 - out of scope here."""
    shop = await session.get(Shop, order.shop_id)
    now = datetime.now(timezone.utc)
    # order.sla_tier is an SLATier enum when freshly loaded from Postgres,
    # but a plain string when set on an un-refreshed ORM instance - getattr
    # normalizes both to the "T2"/"HOT_SHOT" string the hold window and
    # HeldOrder expect.
    tier = getattr(order.sla_tier, "value", order.sla_tier) or "T2"
    hold_minutes = resolve_hold_window_minutes(tier)

    order.delivery_attempts += 1
    order.status = OrderStatus.held
    order.hold_deadline = now + timedelta(minutes=hold_minutes)
    # Clear the prior attempt's failure reason - this order is back in
    # flight, not failed, so client/ops views shouldn't still show why the
    # *last* attempt failed.
    order.failure_reason = None
    # Re-entering dispatch - clear the prior assignment stamp so nothing
    # reads this as still attached to the old, failed stop.
    order.assigned_at = None
    await session.flush()

    await hold_queue.add(
        str(order.hub_id),
        HeldOrder(
            order_id=str(order.id),
            shop_lat=shop.lat,
            shop_lng=shop.lng,
            sla_tier=tier,
            hold_deadline=order.hold_deadline,
            held_since=now,
            shop_name=shop.name,
        ),
    )
