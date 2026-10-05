"""Cancelling an order before it is collected.

A client could place an order from the portal and the API and never take it
back: the only cancel anywhere was ops resolving a *failed* delivery. An order
placed twice, or for a part the shop then found on its own shelf, went out
regardless, and was billed.

**Before collection only.** Received, classified, held or queued, the order is
a row and an entry in the hold queue, and withdrawing it costs nothing. Once a
driver has been assigned, a van may be heading to the shop or carrying the
parts, and taking the order back is a driver-facing operation: the stop has to
come off a route somebody is driving. That is dispatch's call, so this refuses
it and the client is told to ring.
"""
from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.store import HoldQueueStore
from app.models.order import Order, OrderStatus
from app.orders.status_service import advance_orders

logger = structlog.get_logger(__name__)

CANCELLABLE_STATUSES = (
    OrderStatus.received,
    OrderStatus.classified,
    OrderStatus.held,
    OrderStatus.queued,
)


class OrderNotCancellable(Exception):
    """Past the point a client can take it back on their own."""


def why_not_cancellable(order: Order) -> str:
    if order.status == OrderStatus.cancelled:
        return "This order is already cancelled."
    if order.status in (OrderStatus.delivered, OrderStatus.returned):
        return f"This order is {order.status.value} and can't be cancelled."
    return (
        "A driver already has this order, so it can't be cancelled from here. "
        "Call dispatch and they will take it off the route."
    )


async def cancel_before_collection(
    session: AsyncSession, hold_queue: HoldQueueStore, order: Order
) -> Order:
    """Cancel `order`, or raise `OrderNotCancellable` saying why not.

    Commits, then takes the order out of the hold queue. In that order on
    purpose: the queue in Redis is the only copy the planner reads, so an
    order gone from it with its row still `held` would never be dispatched and
    never be seen. A cancelled row still in the queue is caught by the state
    machine, which refuses to assign a cancelled order.
    """
    if order.status not in CANCELLABLE_STATUSES:
        raise OrderNotCancellable(why_not_cancellable(order))
    # Through the state machine, so the client's webhook hears CANCELLED.
    moved = await advance_orders(session, [order.id], OrderStatus.cancelled)
    if not moved:
        raise OrderNotCancellable(why_not_cancellable(order))
    await session.commit()
    await hold_queue.remove(str(order.hub_id), str(order.id))
    logger.info("order_cancelled_before_collection", order_id=str(order.id), hub_id=str(order.hub_id))
    return order
