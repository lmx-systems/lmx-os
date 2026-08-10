"""
Advancing an order's status (docs/LMX_LINK_PLAN.md §1.4).

One function, so that validation, persistence and write-back cannot drift apart.
Before this, order status was written from three different places in
`app/api/driver_routes.py` with a bare `.values(status=...)` and no notion of
whether the transition was legal - which was survivable while the only states
were held/assigned/delivered, and stops being survivable now that there are
stop-level states an order moves through in sequence.

Why the status enum still gets written with an UPDATE rather than through the
ORM: the existing driver-route code updates orders in bulk by id set (one route
covers several orders), and keeping that shape avoids loading rows purely to
mutate one column on the hot path a driver waits on.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus
from app.orders.sinks import emit_status_change
from app.orders.state_machine import can_transition

logger = structlog.get_logger(__name__)


async def advance_orders(
    session: AsyncSession,
    order_ids: list[uuid.UUID],
    new_status: OrderStatus,
    *,
    occurred_at: datetime | None = None,
) -> list[Order]:
    """Move a set of orders to `new_status`, skipping any that can't legally go.

    Returns the orders that actually moved.

    **An illegal transition is skipped and logged, not raised.** These are driven
    by driver actions on a route that may cover several orders in different
    states - one already-failed order among four should not fail the driver's
    stop completion, and a retried offline action replaying an old transition is
    ordinary rather than exceptional. The state machine's job here is to stop
    nonsense being written, not to police the caller.

    Does NOT commit. The caller owns the transaction, because these transitions
    always accompany other writes (a stop completing, a PoD being recorded) and
    splitting the commit would let status and reality disagree.
    """
    if not order_ids:
        return []

    occurred_at = occurred_at or datetime.now(timezone.utc)
    # populate_existing is load-bearing, not a micro-optimization. Order status
    # is written from more than one session - the optimizer's dispatch cycle
    # sets `assigned` in its own transaction - so an object already in this
    # session's identity map can be stale by the time a driver action reaches
    # here. Without this the machine validates against a status the row no
    # longer has and silently skips every legal transition, which is exactly the
    # failure that surfaced when the driver app's core loop left an order on
    # `held` through accept, pickup and delivery.
    rows = (
        (
            await session.execute(
                select(Order)
                .where(Order.id.in_(order_ids))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )

    moved: list[Order] = []
    for order in rows:
        if order.status == new_status:
            continue  # idempotent - a replayed offline action, not an error
        if not can_transition(order.status, new_status):
            logger.info(
                "order_status_transition_skipped",
                order_id=str(order.id),
                current=order.status.value,
                requested=new_status.value,
            )
            continue

        previous = order.status
        order.status = new_status
        if new_status == OrderStatus.delivered and order.delivered_at is None:
            # Ground truth (docs/ROADMAP.md I1): written once, never on update.
            order.delivered_at = occurred_at
        moved.append(order)

        await emit_status_change(
            # The caller's uncommitted transaction, so a sink can record an owed
            # notification atomically with the transition - see app/orders/sinks.py.
            session=session,
            order_id=str(order.id),
            client_id=str(order.client_id) if order.client_id else None,
            source_system=order.source_system,
            source_order_ref=order.source_order_ref,
            previous=previous,
            current=new_status,
            occurred_at=occurred_at,
        )

    return moved
