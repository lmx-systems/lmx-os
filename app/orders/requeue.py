"""Putting an offer's orders back when the offer goes nowhere.

Lived in app/api/driver_routes.py, where a decline and a lapse call it. Dispatch
withdrawing an offer to cancel one of its orders (app/orders/cancellation.py)
needs the same move for the offer's other orders, so it lives here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.fleet_state.manager import FleetStateManager
from app.models.order import INTAKE_LIVE, Order, OrderStatus
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.optimizer.event_trigger import dispatch_event_bus
from app.orders.sinks import emit_status_change

logger = structlog.get_logger(__name__)

# A stop nobody will drive to again: done, failed, taken off the route, or a
# delivery logged off-platform (`DE-7`). The driver app's terminal set.
_FINISHED_STOP_STATUSES = ("completed", "failed", "cancelled", "recorded")


async def requeue_orders_from_offer(
    session: AsyncSession,
    hub_id: str,
    driver_id: str,
    stop_payload: list[dict],
    *,
    free_driver: bool = True,
    wake: bool = True,
) -> None:
    """
    A declined/expired offer never touches Route/Stop - the orders just go
    back to the hold queue (Redis) with their original geography/SLA tier
    so the next Dispatch Optimizer cycle tries again, and Order.status
    reverts from "assigned" (set optimistically the moment the optimizer
    proposed the offer - see app/optimizer/service.py) back to "held" -
    not "queued", which per this enum's own definition means "released
    from hold, waiting for a route assignment." The order is neither of
    those right now; it's back in the same Redis hold queue app/ingestion/
    service.py uses "held" for, so the Postgres status should say so too.

    Also puts the driver back in the optimizer's assignable pool - the
    optimizer took them out of it the moment it made the offer (see
    app/optimizer/service.py) precisely so they can't be offered a second,
    overlapping job while this one is still pending.

    **Called once per offer.** Every caller moves the offer out of `offered`
    with the row locked first (decline, accept's lapse check, the expiry sweep,
    the driver's offer list, cancel's withdrawal), so a second, stale copy of
    the same offer finds it answered and sends nothing back. Only that makes
    "still `assigned`" mean "still assigned on this offer": an order this offer
    lost and dispatch gave to somebody else is `assigned` too.

    `wake=False` sends them back without waking the hub's cycle, and without
    the hold queue's wake-up for an order whose hold deadline has already
    passed - that wake-up fired within a second and woke the cycle anyway. For
    the stranded sweep below, which runs just before the scheduled cycle for
    the same hub: that cycle reads them. An order still inside its hold (one
    the failed cycle released early, for a partner or a passing driver) keeps
    its wake-up: the cycle after the sweep may hold it again, and the cycle
    removed its wake-up when it planned it, so without one nothing looked at it
    again until the next sweep or an unrelated event.

    `free_driver=False` for orders an offer never reached: the cycle moved them
    to `assigned` for a driver whom somebody else claimed before the cycle could
    (app/optimizer/service.py). That driver's `offered` belongs to the other
    offer, and must stand.
    """
    if free_driver:
        # Only from `offered`, in one step: a driver who went on break between a
        # read and a write here was put back in the pool.
        await FleetStateManager().set_status_if(
            hub_id, driver_id, expect_status="offered", new_status="available"
        )

    hold_queue = HoldQueueStore()
    now = datetime.now(timezone.utc)
    order_ids = [uuid.UUID(s["order_id"]) for s in stop_payload]
    # Only orders still `assigned`. An order dispatch cancelled, or assigned to
    # somebody else by hand, since this offer was made is not this offer's to
    # send back: the UPDATE used to set `held` whatever the status was, and put a
    # cancelled order back in the queue for the next cycle to dispatch.
    requeued_ids = (
        set(
            await session.scalars(
                update(Order)
                .where(Order.id.in_(order_ids), Order.status == OrderStatus.assigned)
                .values(status=OrderStatus.held)
                .returning(Order.id)
                .execution_options(synchronize_session=False)
            )
        )
        if order_ids
        else set()
    )
    orders_result = (
        await session.execute(
            select(Order)
            .where(Order.id.in_(requeued_ids))
            .execution_options(populate_existing=True)
        )
        if requeued_ids
        else None
    )
    orders_by_id = {o.id: o for o in (orders_result.scalars().all() if orders_result else [])}

    # The client was told ASSIGNED, so it's told the order is back. The UPDATE
    # above bypasses advance_orders on purpose - the state machine has no
    # assigned -> held, since only a decline or a lapse may make that move - so
    # nothing else would emit it.
    for requeued in orders_by_id.values():
        await emit_status_change(
            session=session,
            order_id=str(requeued.id),
            client_id=str(requeued.client_id) if requeued.client_id else None,
            source_system=requeued.source_system,
            source_order_ref=requeued.source_order_ref,
            previous=OrderStatus.assigned,
            current=OrderStatus.held,
            occurred_at=now,
        )

    for stop in stop_payload:
        order = orders_by_id.get(uuid.UUID(stop["order_id"]))
        if order is None:
            continue  # not this offer's to send back; see above
        if order.intake_mode != INTAKE_LIVE:
            # History is held on purpose and never queued; nothing should have
            # offered it, and if anything did, the queue is not where it goes.
            continue
        hold_deadline = order.hold_deadline or (now + timedelta(minutes=5))
        await hold_queue.add(
            hub_id,
            schedule_wakeup=wake or hold_deadline > now,
            order=HeldOrder(
                order_id=stop["order_id"],
                shop_lat=stop["lat"],
                shop_lng=stop["lng"],
                sla_tier=stop["sla_tier"],
                # Deliberately reuses the order's original hold_deadline
                # (very likely already in the past by now) rather than
                # inventing a fresh one - that makes the next hold cycle's
                # "past SLA deadline" rule force-release it immediately
                # instead of holding it all over again behind the driver
                # who just declined.
                hold_deadline=hold_deadline,
                held_since=now,
                shop_name=stop.get("shop_name", ""),
                # Read off the order rather than the offer payload: the offer only
                # ever carried pickup coordinates. Without this a declined or
                # lapsed order would go back into the queue having lost its
                # delivery location, so the next cycle would plan half its journey
                # (app/optimizer/google_routes_client.py::_build_request).
                delivery_lat=(
                    float(order.delivery_lat)
                    if order is not None and order.delivery_lat is not None
                    else None
                ),
                delivery_lng=(
                    float(order.delivery_lng)
                    if order is not None and order.delivery_lng is not None
                    else None
                ),
            ),
        )
    if wake:
        await dispatch_event_bus.publish(hub_id, "job_offer_lapsed")


async def return_stranded_assignments(
    session: AsyncSession, hub_id: str, *, now: datetime | None = None
) -> int:
    """Send back orders `assigned` to nobody, and return how many (`CON-5`).

    A cycle moves its orders to `assigned` and commits, then makes their offers
    in a second transaction. When that one fails, the drivers it claimed are
    given back but the orders stay `assigned`, with no offer, no stop and out of
    the hold queue: nobody has them and nothing says so. Sending them back from
    the cycle's failure path re-ran the cycle into the same failure about once a
    second, each order flipping between `assigned` and `held` with a client
    webhook each time. So the scheduled sweep does it instead, under the hub
    lock, before its cycles (app/api/internal_routes.py), and does not wake the
    cycle itself: a failure that recurs costs one round trip per order per
    sweep, every five minutes, not one a second.

    Stranded means: `assigned`, live, unchanged for the grace, on no open offer,
    and on no stop still to be done. Locked with `SKIP LOCKED`, so an order
    somebody is writing is left for the next sweep, and re-checked by the
    requeue's own `WHERE status = 'assigned'`. Commits nothing; the caller does.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.stranded_assignment_grace_seconds)
    candidates = list(
        (
            await session.execute(
                select(Order)
                .where(
                    Order.hub_id == uuid.UUID(hub_id),
                    Order.status == OrderStatus.assigned,
                    Order.intake_mode == INTAKE_LIVE,
                    Order.updated_at < cutoff,
                )
                .order_by(Order.id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    if not candidates:
        return 0
    candidate_ids = [order.id for order in candidates]
    open_payloads: list[list[dict]] = list(
        await session.scalars(
            select(RouteOffer.stop_payload).where(
                RouteOffer.hub_id == uuid.UUID(hub_id), RouteOffer.status == "offered"
            )
        )
    )
    on_an_offer = {stop.get("order_id") for stops in open_payloads for stop in (stops or [])}
    # A stop still to be done. A redelivery keeps its failed attempt's stops as
    # history (app/delivery/resolution.py) - a completed pickup, a failed
    # drop-off - and counting those left a redelivered order a failed cycle
    # stranded `assigned` for good: on a stop, by this test, that nobody will
    # ever drive to.
    on_a_stop = set(
        await session.scalars(
            select(StopOrder.order_id)
            .join(Stop, Stop.id == StopOrder.stop_id)
            .where(
                StopOrder.order_id.in_(candidate_ids),
                Stop.status.notin_(_FINISHED_STOP_STATUSES),
            )
        )
    )
    stranded = [
        order
        for order in candidates
        if str(order.id) not in on_an_offer and order.id not in on_a_stop
    ]
    if not stranded:
        return 0
    shops = {
        shop.id: shop
        for shop in await session.scalars(
            select(Shop).where(Shop.id.in_({order.shop_id for order in stranded}))
        )
    }
    payload: list[dict] = []
    for order in stranded:
        shop = shops.get(order.shop_id) if order.shop_id is not None else None
        if shop is None:
            continue  # nowhere to collect from; left for a person, as before
        payload.append(
            {
                "order_id": str(order.id),
                "lat": shop.lat,
                "lng": shop.lng,
                "sla_tier": getattr(order.sla_tier, "value", order.sla_tier) or "T2",
                "shop_name": shop.name,
            }
        )
    if not payload:
        return 0
    # No driver to free: whoever was claimed for these was given back when the
    # offers failed, and a driver `offered` now belongs to some other offer.
    await requeue_orders_from_offer(
        session, hub_id, "", payload, free_driver=False, wake=False
    )
    logger.warning("dispatch_stranded_assignments_returned", hub_id=hub_id, orders=len(payload))
    return len(payload)
