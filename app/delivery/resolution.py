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
from app.orders.status_service import advance_orders
from app.sla.engine import resolve_hold_window_minutes

REDELIVER = "redeliver"
RETURN_TO_SHOP = "return_to_shop"
CANCEL = "cancel"
RESOLUTION_ACTIONS = (REDELIVER, RETURN_TO_SHOP, CANCEL)


class OrderNotFailedError(Exception):
    """Only an order currently in delivery_failed can be resolved - a
    delivered/cancelled/in-flight order has no failure to resolve."""


class ShopMissingError(Exception):
    """The order points at a shop that is not there.

    Its own exception rather than reusing `OrderNotFailedError`, whose name
    would then be lying about half the cases it covers. A redelivery needs
    somewhere to re-pick from, and `shop_id` is a non-nullable FK - so this is
    a broken row, not an ordinary miss, and the operator should be told that
    rather than shown a 409 about a failure state that is perfectly fine.
    """


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
        await _move(session, order, OrderStatus.returned)
    elif action == CANCEL:
        await _move(session, order, OrderStatus.cancelled)
    else:
        raise ValueError(f"Unknown resolution action: {action!r}")
    await session.commit()
    return order


async def _move(session: AsyncSession, order: Order, status: OrderStatus) -> None:
    """Through the state machine, so the status sinks hear it.

    Written directly, as these were, no client's webhook was ever told that a
    failed order had been returned, cancelled or put back in the queue - the
    last thing it heard was that the delivery had failed.
    """
    moved = await advance_orders(session, [order.id], status)
    if not moved:
        # Unreachable while the caller checks for delivery_failed first; loud
        # rather than a resolution that silently changed nothing.
        raise OrderNotFailedError(
            f"Order {order.id} could not move from '{order.status.value}' to '{status.value}'"
        )


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
    if shop is None:
        # `order.shop_id` is a non-nullable FK, so this is a broken row rather
        # than an ordinary miss - and redelivering is an ops action taken on an
        # order that has ALREADY failed once. Letting it raise `AttributeError:
        # 'NoneType' object has no attribute 'lat'` three lines down would tell
        # the operator nothing about which order or why, at the moment they are
        # trying to rescue a delivery. Found by mypy, which had been advisory
        # since it was added and reported this among 196 findings that were not
        # this.
        raise ShopMissingError(
            f"Order {order.id} points at shop {order.shop_id}, which does not exist - "
            "there is nowhere to re-pick the parts from"
        )
    now = datetime.now(timezone.utc)
    # order.sla_tier is an SLATier enum when freshly loaded from Postgres,
    # but a plain string when set on an un-refreshed ORM instance - getattr
    # normalizes both to the "T2"/"HOT_SHOT" string the hold window and
    # HeldOrder expect.
    tier = getattr(order.sla_tier, "value", order.sla_tier) or "T2"
    hold_minutes = resolve_hold_window_minutes(tier)

    order.delivery_attempts += 1
    order.hold_deadline = now + timedelta(minutes=hold_minutes)
    # Clear the prior attempt's failure reason - this order is back in
    # flight, not failed, so client/ops views shouldn't still show why the
    # *last* attempt failed.
    order.failure_reason = None
    # Re-entering dispatch - clear the prior assignment stamp so nothing
    # reads this as still attached to the old, failed stop.
    order.assigned_at = None
    await session.flush()
    await _move(session, order, OrderStatus.held)

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
            delivery_lat=float(order.delivery_lat) if order.delivery_lat is not None else None,
            delivery_lng=float(order.delivery_lng) if order.delivery_lng is not None else None,
        ),
    )
