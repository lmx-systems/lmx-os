"""
Marking the stop a driver is actually on their way to (docs/ROADMAP.md L11).

**Two states in this codebase were declared and never reached.** `Stop.status`
documents `pending | en_route | arrived | completed | failed` and nothing ever wrote
`en_route`; `OrderStatus.en_route_drop` is in the enum and in
`app/orders/state_machine.py`'s transition map, and nothing ever advanced an order
into it. So a client watching their delivery went `PICKED_UP -> DELIVERED`, and F3's
tracking page had to derive "your driver is on the way" from the stop rows because the
status could not be trusted to say it.

**Why the obvious trigger was rejected.** L11's own note says stamping `en_route_drop`
when the pickup completes would be a meaningless timestamp - and on a multi-stop route
it is worse than meaningless. A driver who collects four orders and drives to the first
customer is not en route to the fourth; marking all four would tell three clients their
driver is inbound while he is thirty minutes away, which is exactly the sort of
promise F3 exists to keep honest.

**The signal used instead is the stop sequence.** A route's *current* stop is its
earliest non-terminal one; when that becomes a given dropoff, every stop before it is
finished, so the driver genuinely is heading there next. That is true for the
single-order case too - completing the only pickup does mean you are on your way to the
only drop - so this is not the rejected trigger in disguise, it is the same instant
being correct for a different reason.

**Deliberately not gated on live position.** F1 makes "has the van actually moved away
from the shop" answerable, and it would be more precise. It would also mean a driver
whose app has not pinged never leaves `picked_up`, stranding the status on a device
problem. Precision that fails closed on a missing GPS fix is the wrong trade for a
field this client-facing; the sequence is always available.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import OrderStatus
from app.models.stop import Stop, StopOrder
from app.orders.status_service import advance_orders

logger = structlog.get_logger(__name__)

# Mirrors app/api/driver_routes.py's set. Duplicated rather than imported so this
# module doesn't depend on the API layer.
_TERMINAL_STOP_STATUSES = ("completed", "failed")


async def mark_current_stop_en_route(session: AsyncSession, route_id) -> Stop | None:
    """Mark the route's current stop as the one being driven to, and return it.

    Called wherever the current stop can change: a route being accepted, a stop being
    completed, a stop being flagged. Each of those finishes one stop and promotes the
    next, and the promoted stop is by definition where the driver goes now.

    Idempotent - a stop already `en_route` or `arrived` is left alone. Re-marking an
    arrived stop as en route would walk the driver's own progress backwards, and it is
    a real case: flagging one stop on a route can run this while another is already
    arrived at.

    Does not commit; the caller owns the transaction, so a status that says the driver
    is inbound cannot outlive the action that made it true.
    """
    result = await session.execute(
        select(Stop)
        .where(Stop.route_id == route_id, Stop.status.notin_(_TERMINAL_STOP_STATUSES))
        .order_by(Stop.sequence)
        .limit(1)
    )
    current = result.scalar_one_or_none()
    if current is None:
        return None  # route finished

    if current.status == "pending":
        current.status = "en_route"

    if current.stop_type == "dropoff":
        order_ids = [
            row[0]
            for row in (
                await session.execute(
                    select(StopOrder.order_id).where(StopOrder.stop_id == current.id)
                )
            ).all()
        ]
        if order_ids:
            # advance_orders skips anything the machine forbids, so an order already
            # delivered or flagged on this stop is left where it is rather than being
            # dragged back to "on the way".
            moved = await advance_orders(session, order_ids, OrderStatus.en_route_drop)
            if moved:
                logger.info(
                    "orders_en_route_to_drop",
                    stop_id=str(current.id),
                    order_count=len(moved),
                )

    return current
