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
from app.delivery.routes import lock_route
from app.fleet_state.manager import FleetStateManager
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.stop import Stop, StopOrder
from app.orders.requeue import requeue_orders_from_offer
from app.optimizer.event_trigger import dispatch_event_bus
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

# A stop taken off a route by a cancellation. Beside pending, en_route, arrived,
# completed and failed; never a stop the driver reached.
STOP_CANCELLED = "cancelled"


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
        await cancel_before_collection(session, hold_queue, order)
        return BEFORE_COLLECTION
    if order.status not in LIVE_CANCELLABLE_STATUSES:
        raise OrderNotCancellable(why_dispatch_cannot_cancel(order))

    finished: tuple[str, str, str] | None = None
    withdrawn = await _withdraw_offer(session, order)
    if withdrawn is not None:
        how = withdrawn
    else:
        how, finished = await _remove_stops(session, order)
    moved = await advance_orders(session, [order.id], OrderStatus.cancelled)
    if not moved:
        raise OrderNotCancellable(why_dispatch_cannot_cancel(order))
    order_id, hub_id = str(order.id), str(order.hub_id)
    await session.commit()
    await hold_queue.remove(hub_id, order_id)
    if finished is not None:
        await _free_driver(*finished)
    logger.info("order_cancelled_by_dispatch", order_id=order_id, hub_id=hub_id, how=how)
    return how


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


async def _withdraw_offer(session: AsyncSession, order: Order) -> str | None:
    offer = (
        await session.execute(
            select(RouteOffer).where(
                RouteOffer.hub_id == order.hub_id,
                RouteOffer.status == "offered",
                RouteOffer.stop_payload.contains([{"order_id": str(order.id)}]),
            )
        )
    ).scalars().first()
    if offer is None:
        return None
    offer.status = "withdrawn"
    offer.responded_at = datetime.now(timezone.utc)
    others = [stop for stop in offer.stop_payload if stop.get("order_id") != str(order.id)]
    await requeue_orders_from_offer(session, str(offer.hub_id), str(offer.driver_id), others)
    return OFFER_WITHDRAWN


async def _remove_stops(
    session: AsyncSession, order: Order
) -> tuple[str, tuple[str, str, str] | None]:
    """Take the order's stops off its route. Returns how, and - when that left
    the route with nothing to do - its (hub id, driver id, route id), so the
    caller can free the driver once the change is committed."""
    rows = (
        await session.execute(
            select(Stop, Route)
            .join(StopOrder, StopOrder.stop_id == Stop.id)
            .join(Route, Route.id == Stop.route_id)
            .where(StopOrder.order_id == order.id, Route.status == "active")
            .order_by(Stop.sequence)
        )
    ).all()
    if not rows:
        # Assigned with neither an open offer nor a live route: nothing holds
        # the order, so there is nothing to take it off.
        return STOPS_REMOVED, None
    pickup = next((stop for stop, _ in rows if stop.stop_type == "pickup"), None)
    if pickup is not None and pickup.status in ("arrived", "completed"):
        raise OrderNotCancellable(
            "The driver is at the shop or already has the parts. When they report, "
            "resolve the delivery as a return."
        )
    route = rows[0][1]
    removed: list[str] = []
    for stop, _ in rows:
        others = await session.scalar(
            select(StopOrder).where(StopOrder.stop_id == stop.id, StopOrder.order_id != order.id).limit(1)
        )
        if others is not None:
            # Other orders still visit this stop; this one just isn't among them.
            link = await session.scalar(
                select(StopOrder).where(StopOrder.stop_id == stop.id, StopOrder.order_id == order.id)
            )
            if link is not None:
                await session.delete(link)
            continue
        # The stop was only ever this order's. Marked rather than deleted: the
        # messages sent about it, and any flag or geofence crossing, refer to
        # the row, and a record of a stop that was planned and then cancelled
        # is worth more than a gap. The route view leaves cancelled stops out.
        stop.status = STOP_CANCELLED
        # No arrival to forecast for a visit that won't happen. `planned_eta`
        # stays: it is the record of what was planned.
        stop.eta = None
        removed.append(str(stop.id))
    route.plan_version += 1
    await session.flush()
    # A route whose every stop is now finished is over, the way `complete_stop`
    # and `flag_stop_issue` close one. Left `active`, it held the driver as busy
    # and stayed a candidate for in-flight insertion with nothing ahead of it.
    # Locked first, as the driver's complete and flag paths do, so a driver
    # finishing the last other stop at this moment can't leave it open.
    await lock_route(session, route.id)
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
    await get_client().publish(
        f"driver_route_events:{route.driver_id}",
        json.dumps(
            {
                "type": "route_updated",
                "route_id": str(route.id),
                "plan_version": route.plan_version,
                "change": "stop_removed",
                "affected_stop_ids": removed,
                "message": "Dispatch cancelled an order; its stops are off your route",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
    )
    return STOPS_REMOVED, finished
