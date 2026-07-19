"""
Dispatch Optimizer (component 5) - the piece the design doc credits with
the DPH advantage: it holds all open orders in view simultaneously and
re-optimizes across the full fleet on every meaningful event, instead of
dispatching one order at a time.

Performance target (Section 9): a full cycle must complete in <5 seconds
for a hub with up to 20 drivers / 100 open orders. `run_cycle` measures
wall-clock time end-to-end and logs a warning (does not fail the request)
if the budget is blown, so a regression shows up in logs before it erodes
the DPH advantage in production.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import update

from app.batch_queue.queue import run_hold_cycle
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.db import session_scope
from app.fleet_state.manager import FleetStateManager
from app.models.order import Order, OrderStatus
from app.models.route_offer import RouteOffer
from app.optimizer.google_routes_client import RouteOptimizationClient, get_route_optimization_client
from app.optimizer.last_cycle_store import LastCycleStore
from app.schemas.optimizer import DriverCandidate, LastCycleSnapshot, OptimizationResult, StopCandidate

logger = structlog.get_logger(__name__)


class DispatchOptimizerService:
    def __init__(
        self,
        fleet_state: FleetStateManager | None = None,
        hold_queue: HoldQueueStore | None = None,
        route_client: RouteOptimizationClient | None = None,
        last_cycle_store: LastCycleStore | None = None,
    ) -> None:
        self._fleet_state = fleet_state or FleetStateManager()
        self._hold_queue = hold_queue or HoldQueueStore()
        self._route_client = route_client or get_route_optimization_client()
        self._last_cycle_store = last_cycle_store or LastCycleStore()

    async def run_cycle(self, hub_id: str) -> OptimizationResult:
        cycle_start = time.perf_counter()
        now = datetime.now(timezone.utc)

        fleet_snapshot = await self._fleet_state.get_fleet_snapshot(hub_id)
        held_orders = await self._hold_queue.get_all(hub_id)

        decisions = run_hold_cycle(
            held_orders,
            available_driver_count=len(fleet_snapshot),
            now=now,
        )
        released_order_ids = {d.order_id for d in decisions if d.action == "release"}
        released_orders = [o for o in held_orders if o.order_id in released_order_ids]

        stops = [
            StopCandidate(
                stop_id=order.order_id,
                order_ids=[order.order_id],
                lat=order.shop_lat,
                lng=order.shop_lng,
                weight_units=1.0,  # per-order weight isn't in HeldOrder; refined once
                # the optimizer reads directly from `orders.weight_units` in Phase 1.
                sla_tier=order.sla_tier,
            )
            for order in released_orders
        ]

        drivers: list[DriverCandidate] = []
        for driver_state in fleet_snapshot:
            location = await self._fleet_state.get_driver_location(hub_id, driver_state.driver_id)
            if location is None:
                continue
            drivers.append(
                DriverCandidate(
                    driver_id=driver_state.driver_id,
                    lat=location.lat,
                    lng=location.lng,
                    capacity_remaining_units=max(
                        driver_state.capacity_units - driver_state.load_units, 0
                    ),
                )
            )

        if stops and drivers:
            assignments, unassigned = await self._route_client.optimize(drivers, stops)
        else:
            assignments, unassigned = [], [s.stop_id for s in stops]

        # Only remove from the hold queue what actually got assigned -
        # anything left unassigned (e.g. no driver had capacity) stays held
        # so it's picked up again next cycle rather than silently dropped.
        assigned_stop_ids = {stop_id for a in assignments for stop_id in a.stop_ids}
        for order_id in assigned_stop_ids:
            await self._hold_queue.remove(hub_id, order_id)

        # Write the dispatch back to Postgres so Order.status doesn't stay
        # "held" forever once Redis has moved on - see the comment on
        # Order.assigned_at. stop_id == order_id today (StopCandidate is
        # always built one order per stop - see the loop above); if
        # commingled multi-order stops land later, this still works as-is
        # since assigned_stop_ids would just contain more order ids.
        # Opens its own session rather than taking one as a constructor arg
        # because this runs from two contexts: a request-scoped call
        # (POST /optimizer/{hub_id}/run-cycle) and a background asyncio task
        # with no request of its own (app/optimizer/event_trigger.py).
        if assigned_stop_ids:
            async with session_scope() as session:
                await session.execute(
                    update(Order)
                    .where(Order.id.in_(uuid.UUID(order_id) for order_id in assigned_stop_ids))
                    .values(status=OrderStatus.assigned, assigned_at=datetime.now(timezone.utc))
                )

        # Extend a job offer to each assigned driver rather than handing them
        # a route directly - see app/models/route_offer.py. Order.status is
        # already "assigned" above regardless of what the driver does with
        # the offer; if they decline or let it expire, app/api/driver_routes.py
        # puts the affected orders back in the hold queue for the next cycle
        # rather than leaving them stuck showing "assigned" with nobody
        # actually driving them.
        if assignments:
            stops_by_id = {s.stop_id: s for s in stops}
            shop_name_by_order_id = {o.order_id: o.shop_name for o in released_orders}
            offer_time = datetime.now(timezone.utc)
            async with session_scope() as session:
                for assignment in assignments:
                    offer_stops = []
                    for stop_id in assignment.stop_ids:
                        candidate = stops_by_id.get(stop_id)
                        if candidate is None:
                            continue
                        offer_stops.append(
                            {
                                "order_id": stop_id,
                                "lat": candidate.lat,
                                "lng": candidate.lng,
                                "sla_tier": candidate.sla_tier,
                                "shop_name": shop_name_by_order_id.get(stop_id, ""),
                            }
                        )
                    if not offer_stops:
                        continue
                    session.add(
                        RouteOffer(
                            hub_id=uuid.UUID(hub_id),
                            driver_id=uuid.UUID(assignment.driver_id),
                            status="offered",
                            stop_payload=offer_stops,
                            offered_at=offer_time,
                            expires_at=offer_time + timedelta(seconds=settings.job_offer_ttl_seconds),
                        )
                    )

        duration = time.perf_counter() - cycle_start
        over_budget = duration > settings.optimizer_cycle_budget_seconds
        if over_budget:
            logger.warning(
                "optimizer_cycle_over_budget",
                hub_id=hub_id,
                duration_seconds=round(duration, 3),
                budget_seconds=settings.optimizer_cycle_budget_seconds,
                driver_count=len(drivers),
                stop_count=len(stops),
            )

        logger.info(
            "optimizer_cycle_complete",
            hub_id=hub_id,
            duration_seconds=round(duration, 3),
            assigned_count=len(assigned_stop_ids),
            unassigned_count=len(unassigned),
            engine=self._route_client.engine_name,
        )

        # Every cycle overwrites this hub's snapshot, whether triggered
        # manually or by the event bus - see LastCycleStore's docstring for
        # why a dashboard needs this instead of only trusting whichever
        # caller happened to trigger the cycle.
        await self._last_cycle_store.set(
            LastCycleSnapshot(
                hub_id=hub_id,
                at=datetime.now(timezone.utc),
                engine=self._route_client.engine_name,
                duration_seconds=round(duration, 3),
                assigned_count=len(assigned_stop_ids),
                unassigned_count=len(unassigned),
                over_budget=over_budget,
            )
        )

        return OptimizationResult(
            hub_id=hub_id,
            assignments=assignments,
            unassigned_stop_ids=unassigned,
            engine=self._route_client.engine_name,
            duration_seconds=round(duration, 3),
            over_budget=over_budget,
        )
