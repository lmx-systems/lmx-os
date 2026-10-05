"""Putting an offer's orders back when the offer goes nowhere.

Lived in app/api/driver_routes.py, where a decline and a lapse call it. Dispatch
withdrawing an offer to cancel one of its orders (app/orders/cancellation.py)
needs the same move for the offer's other orders, so it lives here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.fleet_state.manager import FleetStateManager
from app.models.order import Order, OrderStatus
from app.optimizer.event_trigger import dispatch_event_bus
from app.orders.sinks import emit_status_change
from app.schemas.fleet import DriverState


async def requeue_orders_from_offer(
    session: AsyncSession, hub_id: str, driver_id: str, stop_payload: list[dict]
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
    """
    manager = FleetStateManager()
    existing_state = await manager.get_driver_state(hub_id, driver_id)
    if existing_state is not None and existing_state.status == "offered":
        await manager.upsert_driver_state(
            DriverState(
                driver_id=driver_id,
                hub_id=hub_id,
                status="available",
                capacity_units=existing_state.capacity_units,
                load_units=existing_state.load_units,
                current_route_id=existing_state.current_route_id,
            )
        )

    hold_queue = HoldQueueStore()
    now = datetime.now(timezone.utc)
    order_ids = [uuid.UUID(s["order_id"]) for s in stop_payload]
    if order_ids:
        await session.execute(
            update(Order).where(Order.id.in_(order_ids)).values(status=OrderStatus.held)
        )
    orders_result = await session.execute(select(Order).where(Order.id.in_(order_ids))) if order_ids else None
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
        await hold_queue.add(
            hub_id,
            HeldOrder(
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
                hold_deadline=(order.hold_deadline if order else None) or (now + timedelta(minutes=5)),
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
    await dispatch_event_bus.publish(hub_id, "job_offer_lapsed")
