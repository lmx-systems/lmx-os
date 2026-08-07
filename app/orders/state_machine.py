"""
The one order status machine, shared by every sink (docs/LMX_LINK_PLAN.md §1.4).

    RECEIVED -> ACCEPTED -> HELD -> ASSIGNED -> EN_ROUTE_PICKUP -> PICKED_UP
             -> EN_ROUTE_DROP -> DELIVERED

with exception branches EXCEPTION_RAISED, RETURNED_TO_HUB and CANCELLED.

**ADDITIVE, NOT A REPLACEMENT.** The `order_status` Postgres enum already exists
with nine values and live rows in every one of them. Two of the "new" states in
§1.4 are the same thing this codebase already has under a different name:

    §1.4 name          existing value      why reuse rather than add
    ---------------    ----------------    -------------------------------------
    EXCEPTION_RAISED   delivery_failed     Already means "attempted and failed,
                                           ops must decide" (R5). Adding a
                                           second value with the same meaning
                                           would make queries ambiguous forever.
    RETURNED_TO_HUB    returned            Already terminal, already notifies
                                           the shop.

So four values are genuinely new: `accepted`, `en_route_pickup`, `picked_up`,
`en_route_drop`. What they add is *stop-level progress promoted onto the order* -
before this, an order sat at `assigned` from dispatch until delivery, and
progress lived only on its `Stop` rows. A client watching their order could see
"assigned" for an hour with no way to tell whether the driver had collected it.

`classified` and `queued` are retained internal sub-states with no equivalent in
§1.4's public vocabulary. They are real and meaningful inside the pipeline
(classified = tiered but not yet queued; queued = released from hold, awaiting a
route) and sinks should map both to HELD when speaking to a consumer.

The map is explicit rather than inferred from an ordering, for the same reason as
`app/gig_platform/service.py::_ALLOWED_TRANSITIONS`: cancellation and exceptions
are not points on the same line as forward progress.
"""
from __future__ import annotations

from app.models.order import OrderStatus

# What a consumer outside LMX should be told, per status. Sinks map through this
# rather than leaking internal vocabulary - `classified` and `queued` are our
# business, not the customer's.
PUBLIC_LABELS: dict[OrderStatus, str] = {
    OrderStatus.received: "RECEIVED",
    OrderStatus.classified: "HELD",
    OrderStatus.held: "HELD",
    OrderStatus.queued: "HELD",
    OrderStatus.assigned: "ASSIGNED",
    OrderStatus.accepted: "ACCEPTED",
    OrderStatus.en_route_pickup: "EN_ROUTE_PICKUP",
    OrderStatus.picked_up: "PICKED_UP",
    OrderStatus.en_route_drop: "EN_ROUTE_DROP",
    OrderStatus.delivered: "DELIVERED",
    OrderStatus.delivery_failed: "EXCEPTION_RAISED",
    OrderStatus.returned: "RETURNED_TO_HUB",
    OrderStatus.cancelled: "CANCELLED",
}

# Terminal states. Nothing moves out of these.
TERMINAL: frozenset[OrderStatus] = frozenset(
    {OrderStatus.delivered, OrderStatus.returned, OrderStatus.cancelled}
)

# Reachable from anywhere non-terminal: an order can be cancelled or hit an
# exception at any point in its life, which is exactly why these can't be
# expressed as a linear sequence.
_ALWAYS_AVAILABLE: tuple[OrderStatus, ...] = (
    OrderStatus.cancelled,
    OrderStatus.delivery_failed,
)

_FORWARD: dict[OrderStatus, tuple[OrderStatus, ...]] = {
    OrderStatus.received: (OrderStatus.classified, OrderStatus.accepted, OrderStatus.held),
    # An EXTERNAL order is never classified (§1.3), so it reaches held directly.
    OrderStatus.classified: (OrderStatus.held, OrderStatus.queued),
    OrderStatus.held: (OrderStatus.queued, OrderStatus.assigned),
    OrderStatus.queued: (OrderStatus.assigned,),
    OrderStatus.accepted: (OrderStatus.held, OrderStatus.queued, OrderStatus.assigned),
    OrderStatus.assigned: (OrderStatus.en_route_pickup, OrderStatus.picked_up),
    # picked_up is reachable directly: a driver arriving and collecting in one
    # action is normal, and the app does not always emit a separate en-route.
    OrderStatus.en_route_pickup: (OrderStatus.picked_up,),
    OrderStatus.picked_up: (OrderStatus.en_route_drop, OrderStatus.delivered),
    OrderStatus.en_route_drop: (OrderStatus.delivered,),
    # A failed order is not finished - R5's resolution decides whether it is
    # redelivered (back to assigned), returned to the hub, or cancelled.
    OrderStatus.delivery_failed: (
        OrderStatus.assigned,
        OrderStatus.queued,
        OrderStatus.returned,
    ),
    OrderStatus.delivered: (),
    OrderStatus.returned: (),
    OrderStatus.cancelled: (),
}


class InvalidOrderTransition(Exception):
    """A status change that isn't a real step in the lifecycle."""

    def __init__(self, current: OrderStatus, requested: OrderStatus) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"cannot move an order from {current.value} to {requested.value}")


def allowed_next(current: OrderStatus) -> tuple[OrderStatus, ...]:
    """Every status this order could legally move to next."""
    if current in TERMINAL:
        return ()
    extra = tuple(s for s in _ALWAYS_AVAILABLE if s != current)
    return _FORWARD.get(current, ()) + extra


def can_transition(current: OrderStatus, new: OrderStatus) -> bool:
    """Whether this move is legal. Same-status is always allowed - see
    `assert_transition` for why."""
    if current == new:
        return True
    return new in allowed_next(current)


def assert_transition(current: OrderStatus, new: OrderStatus) -> None:
    """Raise unless this transition is real.

    Repeating the current status is deliberately NOT an error. A driver
    double-tapping on a flaky connection, or an offline action replaying out of
    the outbox, should be idempotent rather than a failure - the same reasoning
    that makes `complete_stop` idempotent today.
    """
    if not can_transition(current, new):
        raise InvalidOrderTransition(current, new)


def public_label(status: OrderStatus) -> str:
    """The §1.4 vocabulary name for a status, for anything customer-facing."""
    return PUBLIC_LABELS[status]
