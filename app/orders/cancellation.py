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

import json
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.store import HoldQueueStore
from app.delivery.en_route import mark_current_stop_en_route
from app.delivery.eta import refresh_route_etas
from app.delivery.routes import lock_route, take_orders_off_stops, try_lock_route
from app.fleet_state.manager import FleetStateManager
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.stop import Stop, StopOrder
from app.optimizer.event_trigger import dispatch_event_bus
from app.orders.requeue import requeue_orders_from_offer
from app.orders.status_service import advance_orders
from app.redis_client import get_client

logger = structlog.get_logger(__name__)

CANCELLABLE_STATUSES = (
    OrderStatus.received,
    OrderStatus.classified,
    OrderStatus.held,
    OrderStatus.queued,
)


class OrderNotCancellable(Exception):
    """Past the point a client can take it back on their own."""


# Dispatch can still cancel these: a driver has the order, but nobody has the
# parts yet. `assigned` is an open offer; `en_route_pickup` an accepted route
# whose pickup hasn't happened.
LIVE_CANCELLABLE_STATUSES = (OrderStatus.assigned, OrderStatus.en_route_pickup)

BEFORE_COLLECTION = "before_collection"
OFFER_WITHDRAWN = "offer_withdrawn"
STOPS_REMOVED = "stops_removed"

# Mirrors app/api/driver_routes.py's set, as app/delivery/en_route.py does, so
# this module doesn't depend on the API layer.
_TERMINAL_STOP_STATUSES = ("completed", "failed", "cancelled")

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
    # Through the state machine, so the client's webhook hears CANCELLED. Only
    # from a status a client may cancel: a cycle assigning the order since the
    # read above leaves this write nothing to match, and the client is told a
    # driver has it rather than cancelling an order with an offer out (`CON-5`).
    moved = await advance_orders(
        session, [order.id], OrderStatus.cancelled, only_from=CANCELLABLE_STATUSES
    )
    if not moved:
        raise OrderNotCancellable(why_not_cancellable(order))
    await session.commit()
    await hold_queue.remove(str(order.hub_id), str(order.id))
    logger.info("order_cancelled_before_collection", order_id=str(order.id), hub_id=str(order.hub_id))
    return order


def why_dispatch_cannot_cancel(order: Order) -> str:
    if order.status == OrderStatus.cancelled:
        return "This order is already cancelled."
    if order.status in (OrderStatus.delivered, OrderStatus.returned):
        return f"This order is {order.status.value} and can't be cancelled."
    return (
        "The parts are on the van. Cancelling now would leave them with no "
        "destination; when the driver reports, resolve the delivery as a return."
    )


async def cancel_live_order(
    session: AsyncSession, hold_queue: HoldQueueStore, order: Order
) -> str:
    """Dispatch's cancel: everything a client's can, plus an order a driver has
    but hasn't collected. Returns how it was done (`BEFORE_COLLECTION`,
    `OFFER_WITHDRAWN` or `STOPS_REMOVED`), or raises `OrderNotCancellable`.

    An open offer carrying the order is withdrawn; the offer's other orders go
    back to the hold queue the way a decline sends them, and the driver is
    offerable again. An accepted route loses the order's stops, if its pickup
    hasn't been reached, and the driver's app is told. Once the driver is at
    the shop or has the parts, this refuses: the parts need a destination, and
    that is a return, not a cancellation.
    """
    if order.status in CANCELLABLE_STATUSES:
        await _cancel_before_collection_for_dispatch(session, hold_queue, order)
        return BEFORE_COLLECTION
    if order.status not in LIVE_CANCELLABLE_STATUSES:
        raise OrderNotCancellable(why_dispatch_cannot_cancel(order))

    finished: tuple[str, str, str] | None = None
    notice: dict | None = None
    withdrawn = await _withdraw_offer(session, order)
    if withdrawn is not None:
        how = withdrawn
    else:
        how, finished, notice = await _remove_stops(session, order)
        if notice is None:
            # Assigned with neither an open offer nor a stop: the cycle may be
            # between its status write and its offers' commit, or an in-flight
            # insertion between its status write and its stops' commit. The
            # offers hold their orders locked to that commit
            # (app/optimizer/service.py) and the insertion holds the row it
            # wrote, so taking this one for update waits for either; then an
            # offer that went out is withdrawn and stops that were added come
            # off, and an offer not yet made reads the order cancelled and
            # leaves it out (`CON-5`).
            await _lock_order(session, order)
            if order.status in CANCELLABLE_STATUSES:
                # The cycle's offer to a driver it found taken or switched off
                # sent the order back while this waited.
                await _cancel_before_collection_for_dispatch(session, hold_queue, order)
                return BEFORE_COLLECTION
            # Without waiting on the offer: holding the order now, waiting on a
            # driver who is answering (and so writing this order) deadlocks.
            withdrawn = await _withdraw_offer(session, order, wait=False)
            if withdrawn is not None:
                how = withdrawn
            else:
                # Holding the order now, so the route is taken without
                # waiting: a driver's complete holds the route and then waits
                # for its stops' orders, and this one may be among them.
                how, finished, notice = await _remove_stops(session, order, wait=False)
    # Only from a status dispatch may cancel: queued behind a driver's pickup,
    # this re-checks once the pickup commits and refuses a collected order
    # (`picked_up` -> `cancelled` is legal for the machine, as a return).
    moved = await advance_orders(
        session, [order.id], OrderStatus.cancelled, only_from=LIVE_CANCELLABLE_STATUSES
    )
    if not moved:
        raise OrderNotCancellable(why_dispatch_cannot_cancel(order))
    order_id, hub_id = str(order.id), str(order.hub_id)
    await session.commit()
    await hold_queue.remove(hub_id, order_id)
    if notice is not None:
        # After the commit, so the app is never told of a removal that a refusal
        # then rolled back.
        await get_client().publish(f"driver_route_events:{notice['driver_id']}", json.dumps(notice["event"]))
    if finished is not None:
        await _free_driver(*finished)
    logger.info("order_cancelled_by_dispatch", order_id=order_id, hub_id=hub_id, how=how)
    return how


async def _cancel_before_collection_for_dispatch(
    session: AsyncSession, hold_queue: HoldQueueStore, order: Order
) -> None:
    """A client's cancel, refused in dispatch's words.

    The order read as waiting, and the guarded write found it gone to a driver:
    a cycle or an in-flight insertion moved it in between. The client's refusal
    says "call dispatch", which is no use to the dispatcher; the live path now
    applies, so cancelling again takes it off the driver.
    """
    try:
        await cancel_before_collection(session, hold_queue, order)
    except OrderNotCancellable as exc:
        # The write matched nothing; what the row is now says why.
        await session.refresh(order)
        if order.status in LIVE_CANCELLABLE_STATUSES:
            raise OrderNotCancellable(
                "A driver was given this order a moment ago. Cancel again to take it off them."
            ) from exc
        if order.status in CANCELLABLE_STATUSES:
            raise
        raise OrderNotCancellable(why_dispatch_cannot_cancel(order)) from exc


async def _free_driver(hub_id: str, driver_id: str, route_id: str) -> None:
    """The driver's route is over: offerable again, and the dispatcher told, as
    `complete_stop` does when a route's last stop is done. After the commit, so
    the cycle this wakes reads the route as finished.

    Only a driver who was working this route. `complete_stop` frees the driver
    unconditionally because the driver just tapped Complete; here dispatch acted
    from a desk, and the driver may have gone on break or off shift since - and
    going `available` on their own passes the document check this would skip.
    Their status is theirs; the route just isn't any more.
    """
    manager = FleetStateManager()
    state = await manager.get_driver_state(hub_id, driver_id)
    if state is not None and state.current_route_id == route_id:
        state.current_route_id = None
        if state.status == "en_route":
            state.status = "available"
        await manager.upsert_driver_state(state)
        await dispatch_event_bus.publish(hub_id, "driver_status_changed")


async def _lock_order(session: AsyncSession, order: Order) -> None:
    """Take the order's row for update and read it fresh."""
    await session.execute(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _withdraw_offer(session: AsyncSession, order: Order, *, wait: bool = True) -> str | None:
    # Locked and read fresh. A driver accepting or declining holds the row, and
    # this waits for them: then the offer is no longer `offered`, nothing is
    # withdrawn, and the caller takes the order off the route an accept built.
    # Unlocked, a stale copy marked an accepted offer `withdrawn` over the
    # accept, and sent back orders a decline had already sent back - by then
    # possibly given to somebody else.
    open_offer = select(RouteOffer).where(
        RouteOffer.hub_id == order.hub_id,
        RouteOffer.status == "offered",
        RouteOffer.stop_payload.contains([{"order_id": str(order.id)}]),
    )
    offer = (
        await session.execute(
            open_offer.with_for_update(skip_locked=not wait).execution_options(populate_existing=True)
        )
    ).scalars().first()
    if offer is None:
        if not wait and await session.scalar(open_offer.with_only_columns(RouteOffer.id).limit(1)):
            raise OrderNotCancellable(
                "The driver is answering the offer for this order right now. Try again in a moment."
            )
        return None
    offer.status = "withdrawn"
    offer.responded_at = datetime.now(timezone.utc)
    others = [stop for stop in offer.stop_payload if stop.get("order_id") != str(order.id)]
    await requeue_orders_from_offer(session, str(offer.hub_id), str(offer.driver_id), others)
    return OFFER_WITHDRAWN


async def _remove_stops(
    session: AsyncSession, order: Order, *, wait: bool = True
) -> tuple[str, tuple[str, str, str] | None, dict | None]:
    """Take the order's stops off its route. Returns how; when that left the
    route with nothing to do, its (hub id, driver id, route id), so the caller
    can free the driver once the change is committed; and the driver app's
    notice of the change, for the caller to send after the commit. The notice
    is None when the order is on no live route.

    `wait=False` when the caller already holds the order's row: then the route
    is taken without waiting, and a busy one refuses with "try again" (see
    docs/ARCHITECTURE.md, lock order).
    """
    # The route first, then its stops, then orders - the order every path that
    # changes a live route takes (docs/ARCHITECTURE.md). Each of them writes the
    # ETAs of the route's other stops, so a cancel that held its own stops while
    # waiting for the route deadlocked against a driver's complete holding the
    # route and writing those stops' ETAs.
    #
    # Only stops still to be done. A redelivery keeps its failed attempt's
    # stops as history (app/delivery/resolution.py) - a completed pickup, a
    # failed drop-off - possibly on another route still active. Counted, they
    # picked the wrong route (`limit(1)` with no order), refused the cancel as
    # "already has the parts" over a pickup collected on the attempt that
    # failed, or rewrote the history to `cancelled`. Newest first, so the
    # attempt being driven now is the one taken off.
    route_id = await session.scalar(
        select(Stop.route_id)
        .join(StopOrder, StopOrder.stop_id == Stop.id)
        .join(Route, Route.id == Stop.route_id)
        .where(
            StopOrder.order_id == order.id,
            Route.status == "active",
            Stop.status.notin_(_TERMINAL_STOP_STATUSES),
        )
        .order_by(Stop.created_at.desc(), Stop.id)
        .limit(1)
    )
    if route_id is None:
        # Assigned with neither an open offer nor a live route: nothing holds
        # the order, so there is nothing to take it off.
        return STOPS_REMOVED, None, None
    if wait:
        await lock_route(session, route_id)
    elif not await try_lock_route(session, route_id):
        raise OrderNotCancellable(
            "The driver is updating this route right now. Try again in a moment."
        )
    # The stops locked and read fresh, in sequence, before the pickup's status
    # is trusted. A driver completing the pickup holds the route, and this
    # waited for them, so it sees the pickup collected. Read unlocked, a stale
    # `en_route` pickup was taken off a route the driver had just collected
    # from (`CON-5`). Re-checked against the route's status: the driver may have
    # finished it while this waited.
    #
    # All the order's stops on the route are locked, finished ones included, so
    # a pickup the driver completed while this waited is seen; only the
    # unfinished ones are taken off.
    locked = (
        await session.execute(
            select(Stop, Route)
            .join(StopOrder, StopOrder.stop_id == Stop.id)
            .join(Route, Route.id == Stop.route_id)
            .where(StopOrder.order_id == order.id, Route.id == route_id, Route.status == "active")
            .order_by(Stop.sequence)
            .with_for_update(of=Stop)
            .execution_options(populate_existing=True)
        )
    ).all()
    rows = [(stop, route) for stop, route in locked if stop.status not in _TERMINAL_STOP_STATUSES]
    if not rows:
        return STOPS_REMOVED, None, None
    # The parts are on the van, or the driver is at the counter: the pickup
    # being driven to is reached, or the open drop-off's own pickup - the last
    # of the order's pickups before it on this route - was collected, perhaps
    # while this waited. A finished pickup with its finished drop-off is a
    # failed attempt's history, not this one; and a drop-off whose pickup was
    # flagged never had parts to deliver (flagging takes it off since `CON-5`,
    # and one left by older code must not hold the cancel).
    pickup = next((stop for stop, _ in rows if stop.stop_type == "pickup"), None)
    dropoff = next((stop for stop, _ in rows if stop.stop_type == "dropoff"), None)
    collected = False
    if pickup is None and dropoff is not None:
        before = [
            stop for stop, _ in locked if stop.stop_type == "pickup" and stop.sequence < dropoff.sequence
        ]
        collected = bool(before) and before[-1].status == "completed"
    if (pickup is not None and pickup.status == "arrived") or collected:
        raise OrderNotCancellable(
            "The driver is at the shop or already has the parts. When they report, "
            "resolve the delivery as a return."
        )
    route = rows[0][1]
    removed = await take_orders_off_stops(session, [stop for stop, _ in rows], [order.id])
    route.plan_version += 1
    await session.flush()
    # A route whose every stop is now finished is over, the way `complete_stop`
    # and `flag_stop_issue` close one. Left `active`, it held the driver as busy
    # and stayed a candidate for in-flight insertion with nothing ahead of it.
    # The route lock taken above makes a driver finishing the last other stop
    # at this moment wait, then count what this committed.
    live = await session.scalar(
        select(func.count())
        .select_from(Stop)
        .where(Stop.route_id == route.id, Stop.status.notin_(_TERMINAL_STOP_STATUSES))
    )
    finished: tuple[str, str, str] | None = None
    if not live:
        route.status = "completed"
        finished = (str(route.hub_id), str(route.driver_id), str(route.id))
    else:
        # If the stop being driven to was one of the cancelled ones, the next one
        # is where the driver goes now - promoted as completing a stop promotes it.
        await mark_current_stop_en_route(session, route.id)
        # The stops after the cancelled ones are now reached sooner; say so. The
        # walk skips a cancelled stop the way it skips a completed one.
        await refresh_route_etas(session, route.id)
    # The same channel the live insertion uses, so the app sees one kind of change.
    notice = {
        "driver_id": str(route.driver_id),
        "event": {
            "type": "route_updated",
            "route_id": str(route.id),
            "plan_version": route.plan_version,
            "change": "stop_removed",
            "affected_stop_ids": removed,
            "message": "Dispatch cancelled an order; its stops are off your route",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    return STOPS_REMOVED, finished, notice
