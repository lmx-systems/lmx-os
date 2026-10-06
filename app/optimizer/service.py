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

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select

from app.batch_queue.clustering import miles_between
from app.batch_queue.queue import HeldOrder, run_hold_cycle
from app import metrics
from app.batch_queue.store import HoldQueueStore
from app.hub_calendar import is_hub_closed_at
from app.config import settings
from app.db import session_scope
from app.delivery.eta import refresh_route_etas
from app.identity.inherited_dwell import planning_service_minutes, profiles_by_location
from app.record import record_decision
from app.record.decisions import MODE_LIVE
from app.fleet_state.manager import FleetStateManager
from app.messaging.job_offer_notifications import notify_driver_of_new_offer
from app.models.order import Order, OrderStatus
from app.orders.status_service import advance_orders
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.optimizer.google_routes_client import RouteOptimizationClient, get_route_optimization_client
from app.optimizer.last_cycle_store import LastCycleStore
from app.redis_client import get_client
from app.schemas.fleet import DriverState
from app.sla.commitment import delivery_commitment, terms_for_client
from app.travel import minutes_for_miles
from app.schemas.optimizer import (
    CyclePlan,
    DriverCandidate,
    HoldDecisionRecord,
    LastCycleSnapshot,
    OptimizationResult,
    StopCandidate,
)

logger = structlog.get_logger(__name__)


def _position_is_stale(recorded_at: str, now: datetime) -> bool:
    """Whether a driver's last reported position is too old to dispatch to.

    Still on duty in the fleet state but silent means the app was closed: the
    driver can't see an offer, and one sent to them would sit until they opened
    the app again, holding its orders. An unreadable timestamp counts as stale.
    """
    try:
        reported = datetime.fromisoformat(recorded_at)
    except ValueError:
        return True
    if reported.tzinfo is None:
        reported = reported.replace(tzinfo=timezone.utc)
    return now - reported > timedelta(seconds=settings.driver_position_stale_after_seconds)


async def _released_by_dispatcher(held_orders: list[HeldOrder]) -> set[str]:
    """The held orders a dispatcher released (`app/record/overrides.py`).

    Read from Postgres rather than copied into the Redis queue. The override
    commits the order's status together with the reason for it, so the status
    already is the record, and a copy in Redis would be a second write that can
    fail on its own. `queued` means exactly this: nothing else writes it, and the
    order stays in the hold queue until a cycle assigns it.
    """
    order_ids = []
    for held in held_orders:
        try:
            order_ids.append(uuid.UUID(held.order_id))
        except ValueError:
            continue  # no order row could carry a status for it
    if not order_ids:
        return set()
    async with session_scope() as session:
        released = await session.scalars(
            select(Order.id).where(Order.id.in_(order_ids), Order.status == OrderStatus.queued)
        )
        return {str(order_id) for order_id in released}


def _miles(a_lat, a_lng, b_lat, b_lng) -> float | None:
    """Straight-line miles, or None when either end is unknown.

    The same arithmetic billing prices a drop with, reached through the hold
    queue's clustering rather than through billing: the optimizer is core and
    billing is the edge (tests/test_architecture_boundaries.py).
    """
    if a_lat is None or a_lng is None or b_lat is None or b_lng is None:
        return None
    return miles_between(float(a_lat), float(a_lng), float(b_lat), float(b_lng))


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

    async def plan_cycle(self, hub_id: str) -> CyclePlan:
        """Decide what this cycle would do, and change nothing (W9).

        Everything `run_cycle` used to do up to and including the solver call, with
        every mutation left to the caller: no hold-queue removals, no `Order.status`
        writes, no `RouteOffer` rows, no push notifications, no mid-route insertion.
        Safe to call at any time against live state, which is what lets shadow mode
        run beside a human-dispatched scaffold without touching it.

        **Read-only is a property of this method, not a convention.** Adding a write
        here silently turns shadow mode into a second dispatcher fighting the real one
        for the same drivers - `tests/test_shadow_plan_cycle.py` asserts the absence.
        """
        plan_start = time.perf_counter()
        now = datetime.now(timezone.utc)

        # Don't dispatch for a hub that isn't operating today (R6). Return
        # before touching the hold queue so held orders simply wait for the
        # next open day rather than being dropped or assigned to no one.
        async with session_scope() as session:
            if await is_hub_closed_at(session, hub_id, now):
                logger.info("optimizer_cycle_skipped_hub_closed", hub_id=hub_id)
                return CyclePlan(
                    hub_id=hub_id,
                    planned_at=now,
                    hub_closed=True,
                    held_order_count=0,
                    released_order_ids=[],
                    shop_name_by_order_id={},
                    fleet_snapshot=[],
                    stops=[],
                    drivers=[],
                    assignments=[],
                    unassigned_stop_ids=[],
                    engine=self._route_client.engine_name,
                    plan_duration_seconds=round(time.perf_counter() - plan_start, 3),
                )

        fleet_snapshot = await self._fleet_state.get_fleet_snapshot(hub_id)
        held_orders = await self._hold_queue.get_all(hub_id)
        metrics.HOLD_QUEUE_DEPTH.labels(hub_id=hub_id).set(len(held_orders))

        decisions = run_hold_cycle(
            held_orders,
            available_driver_count=len(fleet_snapshot),
            now=now,
            released_by_dispatcher=await _released_by_dispatcher(held_orders),
            driver_passing=await self._held_orders_a_driver_is_passing(hub_id, held_orders),
        )
        released_order_ids = {d.order_id for d in decisions if d.action == "release"}
        released_orders = [o for o in held_orders if o.order_id in released_order_ids]
        # Question 3's releases go onto the passing driver's route, not to the
        # idle-driver solver: they are left unassigned here, and run_cycle's
        # live insertion places them. One that can't be placed after all stays
        # held, as any unassigned stop does.
        collected_on_the_way = {d.order_id for d in decisions if d.reason == "driver_passing"}

        stops = [
            StopCandidate(
                stop_id=order.order_id,
                order_ids=[order.order_id],
                lat=order.shop_lat,
                lng=order.shop_lng,
                # Lets the solver plan pickup -> delivery instead of a single visit
                # at the shop. None when the order has no geocoded drop, which the
                # client handles by falling back to the old single-visit shape
                # rather than refusing to dispatch.
                delivery_lat=order.delivery_lat,
                delivery_lng=order.delivery_lng,
                weight_units=1.0,  # per-order weight isn't in HeldOrder; refined once
                # the optimizer reads directly from `orders.weight_units` in Phase 1.
                sla_tier=order.sla_tier,
                # The committed collection time, so the solver can sequence by urgency
                # instead of only by distance. This is the same value the client portal
                # reports as `collect_by`, which is the promise a counter person read off
                # the confirmation screen.
                collect_by=order.hold_deadline,
            )
            for order in released_orders
        ]

        drivers: list[DriverCandidate] = []
        for driver_state in fleet_snapshot:
            location = await self._fleet_state.get_driver_location(hub_id, driver_state.driver_id)
            if location is None or _position_is_stale(location.recorded_at, now):
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

        for_the_solver = [stop for stop in stops if stop.stop_id not in collected_on_the_way]
        if for_the_solver and drivers:
            assignments, unassigned = await self._route_client.optimize(drivers, for_the_solver)
        else:
            assignments, unassigned = [], [s.stop_id for s in for_the_solver]
        unassigned = list(unassigned) + [s.stop_id for s in stops if s.stop_id in collected_on_the_way]

        return CyclePlan(
            hub_id=hub_id,
            planned_at=now,
            hub_closed=False,
            held_order_count=len(held_orders),
            released_order_ids=[o.order_id for o in released_orders],
            hold_decisions=[
                HoldDecisionRecord(
                    order_id=d.order_id,
                    action=d.action,
                    reason=d.reason,
                    cluster_mate_ids=list(d.cluster_mate_ids),
                )
                for d in decisions
            ],
            shop_name_by_order_id={o.order_id: o.shop_name for o in released_orders},
            fleet_snapshot=fleet_snapshot,
            stops=stops,
            drivers=drivers,
            assignments=assignments,
            unassigned_stop_ids=unassigned,
            engine=self._route_client.engine_name,
            plan_duration_seconds=round(time.perf_counter() - plan_start, 3),
        )

    async def run_cycle(self, hub_id: str) -> OptimizationResult:
        cycle_start = time.perf_counter()

        plan = await self.plan_cycle(hub_id)

        # REC-1: freeze what this cycle saw, before acting on any of it. In its
        # own session and committed immediately, so the record survives a
        # failure in the commit half below - a decision that was made and then
        # failed to execute is exactly the kind a dispute turns on, and losing
        # it with the transaction would lose the interesting ones first.
        #
        # A closed hub is recorded too, with `hub_closed` set. W9's
        # data-completeness metric needs a quiet Sunday to be distinguishable
        # from a cycle that had a chance and took it, and it can only be if
        # both leave a row.
        async with session_scope() as record_session:
            await record_decision(record_session, plan, mode=MODE_LIVE)

        if plan.hub_closed:
            return OptimizationResult(
                hub_id=hub_id,
                assignments=[],
                unassigned_stop_ids=[],
                engine=plan.engine,
                duration_seconds=round(time.perf_counter() - cycle_start, 3),
                over_budget=False,
            )

        # Named to match what the commit half below already called them, so this
        # extraction stayed a move rather than a rewrite.
        stops = plan.stops
        assignments = plan.assignments
        unassigned = list(plan.unassigned_stop_ids)
        fleet_snapshot = plan.fleet_snapshot

        # Live route-change push: anything still unassigned after the
        # normal idle-driver matching above gets a shot at an already-
        # active route with spare capacity, rather than sitting held
        # indefinitely whenever this hub has no idle driver to offer it to
        # right now. See _insert_unassigned_into_active_routes's docstring
        # for what "pushed, not yanked" means here.
        inserted_into_active_routes: set[str] = set()
        if unassigned:
            stops_by_id = {s.stop_id: s for s in stops}
            inserted_into_active_routes = await self._insert_unassigned_into_active_routes(
                hub_id, unassigned, stops_by_id
            )
            unassigned = [order_id for order_id in unassigned if order_id not in inserted_into_active_routes]

        # Only remove from the hold queue what actually got assigned -
        # anything left unassigned (e.g. no driver had capacity) stays held
        # so it's picked up again next cycle rather than silently dropped.
        assigned_stop_ids = {stop_id for a in assignments for stop_id in a.stop_ids} | inserted_into_active_routes
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
                # Through the state machine, so the transition reaches the status
                # sinks. This was a plain UPDATE, and no client ever got ASSIGNED.
                assigned_at = datetime.now(timezone.utc)
                moved = await advance_orders(
                    session,
                    [uuid.UUID(order_id) for order_id in assigned_stop_ids],
                    OrderStatus.assigned,
                    occurred_at=assigned_at,
                )
                for order in moved:
                    order.assigned_at = assigned_at

        # Extend a job offer to each assigned driver rather than handing them
        # a route directly - see app/models/route_offer.py. Order.status is
        # already "assigned" above regardless of what the driver does with
        # the offer; if they decline or let it expire, app/api/driver_routes.py
        # puts the affected orders back in the hold queue for the next cycle
        # rather than leaving them stuck showing "assigned" with nobody
        # actually driving them.
        if assignments:
            stops_by_id = {s.stop_id: s for s in stops}
            shop_name_by_order_id = plan.shop_name_by_order_id
            fleet_by_id = {d.driver_id: d for d in fleet_snapshot}
            offer_time = datetime.now(timezone.utc)
            # (driver_id, stop_count) pairs to push-notify after the offers
            # below are actually committed - sent outside the session block
            # so a driver is never notified about an offer that failed to
            # persist (app/messaging/job_offer_notifications.py).
            offers_to_notify: list[tuple[str, int]] = []
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
                    # The plan itself, alongside the per-order preview. Only visits for
                    # orders that survived the `stops_by_id` filter above, so the two
                    # payloads describe the same set of work rather than disagreeing.
                    offered_order_ids = {s["order_id"] for s in offer_stops}
                    visit_payload = [
                        {
                            "order_id": visit.order_id,
                            "kind": visit.kind,
                            "arrival": visit.arrival.isoformat() if visit.arrival else None,
                        }
                        for visit in assignment.visits
                        if visit.order_id in offered_order_ids
                    ]
                    session.add(
                        RouteOffer(
                            hub_id=uuid.UUID(hub_id),
                            driver_id=uuid.UUID(assignment.driver_id),
                            status="offered",
                            stop_payload=offer_stops,
                            visit_payload=visit_payload,
                            offered_at=offer_time,
                            expires_at=offer_time + timedelta(seconds=settings.job_offer_ttl_seconds),
                        )
                    )
                    offers_to_notify.append((assignment.driver_id, len(offer_stops)))

                    # Take the driver out of the assignable pool the moment
                    # they're offered a job, not just once they accept -
                    # otherwise the very next cycle can offer them a second,
                    # overlapping job before they've responded to the first
                    # (get_fleet_snapshot only excludes non-"available"
                    # drivers). Reverted back to "available" on decline/
                    # expiry (app/api/driver_routes.py); accept moves it
                    # straight to "en_route" instead.
                    existing_state = fleet_by_id.get(assignment.driver_id)
                    if existing_state is not None:
                        await self._fleet_state.upsert_driver_state(
                            DriverState(
                                driver_id=existing_state.driver_id,
                                hub_id=hub_id,
                                status="offered",
                                capacity_units=existing_state.capacity_units,
                                load_units=existing_state.load_units,
                                current_route_id=existing_state.current_route_id,
                            )
                        )

            for driver_id, stop_count in offers_to_notify:
                await notify_driver_of_new_offer(driver_id, stop_count, settings.job_offer_ttl_seconds)

        duration = time.perf_counter() - cycle_start
        over_budget = duration > settings.optimizer_cycle_budget_seconds
        metrics.OPTIMIZER_CYCLE_SECONDS.labels(
            hub_id=hub_id, engine=self._route_client.engine_name
        ).observe(duration)
        if over_budget:
            metrics.OPTIMIZER_CYCLES_OVER_BUDGET.labels(hub_id=hub_id).inc()
            logger.warning(
                "optimizer_cycle_over_budget",
                hub_id=hub_id,
                duration_seconds=round(duration, 3),
                budget_seconds=settings.optimizer_cycle_budget_seconds,
                driver_count=len(plan.drivers),
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

    async def _insert_unassigned_into_active_routes(
        self, hub_id: str, unassigned_order_ids: list[str], stops_by_id: dict[str, StopCandidate]
    ) -> set[str]:
        """
        Live route-change push (v1): before this method existed, nothing
        anywhere ever resequenced or added to a Route already active for a
        driver mid-shift - run_cycle only ever created brand-new RouteOffers
        for idle ("available") drivers. This is the first capability to
        mutate an active route, so the "pushed, not yanked" invariant is
        built in from day one rather than retrofitted: a new stop is only
        ever appended after every stop already on the route (by sequence),
        so the driver's current in-progress stop - and everything before
        it - is never touched, resequenced, or reassigned out from under
        them. The driver is notified via the SSE channel published below
        (app/api/driver_routes.py's GET /driver/me/route-events), never by
        silently changing what GET /driver/me/route returns next time they
        happen to poll it.

        v1 simplifications, called out explicitly rather than left silent:
        one order at a time (no batch-commingling multiple unassigned
        orders into a single new pickup stop even if they share a shop),
        no HOT_SHOT-first resequencing of the newly-appended stops (accept_offer
        does this for a route's *initial* stops; this always appends
        last). Capacity is checked against the driver's live
        DriverState.load_units/capacity_units in Redis (the same ledger
        complete_stop/flag_stop_issue maintain) rather than a stop count -
        a route with no fleet state on file (driver offline/unknown) is
        skipped defensively rather than assumed to have room.

        **Only a driver who is passing** (design doc §6, third question). An
        order goes onto the route whose remaining stops end nearest its pickup,
        within `in_flight_insertion_radius_miles`, and only if the detour still
        lands the order by its promise. Before this it went onto the first
        route with room, wherever that driver was going: a van at the far end
        of the hub picked up a two-mile detour, and the order's own promise was
        never looked at. A route whose end can't be placed - no stop with an
        address and no live position - isn't "passing", and is left alone.
        """
        inserted: set[str] = set()

        async with session_scope() as session:
            routes_result = await session.execute(
                select(Route).where(Route.hub_id == uuid.UUID(hub_id), Route.status == "active")
            )
            active_routes = list(routes_result.scalars().all())
            if not active_routes:
                return inserted

            for order_id in unassigned_order_ids:
                if stops_by_id.get(order_id) is None:
                    continue

                order = await session.get(Order, uuid.UUID(order_id))
                if order is None or order.delivery_lat is None or order.delivery_lng is None:
                    # No delivery address on file yet for this order - can't
                    # generate a dropoff stop for it. accept_offer never hits
                    # this gap since a route is always built from a full
                    # offer payload with both sides already resolved.
                    continue
                shop = await session.get(Shop, order.shop_id)
                if shop is None:
                    continue

                best = await self._passing_route(
                    session, hub_id, order, shop, stops_by_id[order_id].weight_units, active_routes
                )

                if best is None:
                    logger.info(
                        "route_stop_insertion_no_passing_driver",
                        hub_id=hub_id,
                        order_id=order_id,
                        active_routes=len(active_routes),
                    )
                    continue
                detour_miles, route, at_pickup, at_drop = best

                next_sequence = (
                    (await session.execute(select(func.max(Stop.sequence)).where(Stop.route_id == route.id))).scalar_one()
                    or 0
                ) + 1

                pickup = Stop(
                    route_id=route.id,
                    shop_id=order.shop_id,
                    sequence=next_sequence,
                    stop_type="pickup",
                    parcel_count=1,
                    planned_eta=at_pickup,
                )
                session.add(pickup)
                await session.flush()
                session.add(StopOrder(stop_id=pickup.id, order_id=order.id))

                dropoff = Stop(
                    route_id=route.id,
                    shop_id=None,
                    sequence=next_sequence + 1,
                    stop_type="dropoff",
                    parcel_count=1,
                    planned_eta=at_drop,
                )
                session.add(dropoff)
                await session.flush()
                session.add(StopOrder(stop_id=dropoff.id, order_id=order.id))

                route.plan_version += 1
                # Through the state machine, as the cycle's own assignments are
                # since #168: a plain UPDATE here meant a client never heard that
                # an inserted order had been assigned.
                for moved in await advance_orders(session, [order.id], OrderStatus.assigned):
                    moved.assigned_at = datetime.now(timezone.utc)
                # The live ETA too, not only the plan. Until now the two new stops
                # carried `eta = None` until the driver's next tap, so the app
                # showed nothing and the portal fell back to a guess for an order
                # that was on a route. `planned_eta` set above is left alone.
                await session.flush()
                await refresh_route_etas(session, route.id)
                await session.commit()

                event_payload = {
                    "type": "route_updated",
                    "route_id": str(route.id),
                    "plan_version": route.plan_version,
                    "change": "stop_added",
                    "affected_stop_ids": [str(pickup.id), str(dropoff.id)],
                    "message": "New stop added ahead on your route",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                }
                await get_client().publish(f"driver_route_events:{route.driver_id}", json.dumps(event_payload))

                logger.info(
                    "route_stop_inserted_live",
                    hub_id=hub_id,
                    route_id=str(route.id),
                    driver_id=str(route.driver_id),
                    order_id=order_id,
                    plan_version=route.plan_version,
                    detour_miles=round(detour_miles, 2),
                )

                inserted.add(order_id)

        return inserted

    async def _passing_route(
        self, session, hub_id: str, order: Order, shop: Shop, weight: float, active_routes: list[Route]
    ) -> tuple[float, Route, datetime, datetime] | None:
        """The active route whose end passes nearest this order's pickup, with
        room for it and time to make its promise: (detour miles, route, planned
        arrival at the pickup, planned arrival at the drop). None when no route
        is passing - the design doc's §6 third question, answered for one order.
        """
        radius = settings.in_flight_insertion_radius_miles
        # A string either way: an enum when freshly loaded, a plain string on an
        # un-refreshed instance (see app/delivery/resolution.py).
        tier = str(getattr(order.sla_tier, "value", order.sla_tier) or "")
        term = (
            (await terms_for_client(session, order.client_id)).get(tier)
            if order.client_id is not None and tier
            else None
        )
        promise = delivery_commitment(order, term).promised_delivery_by
        pickup_to_drop = minutes_for_miles(
            _miles(shop.lat, shop.lng, order.delivery_lat, order.delivery_lng) or 0.0
        )
        # Time at the shop's counter: its dock's observed median where we have
        # one, the placeholder otherwise - the same figure the ETA walk will
        # write for the stop, so the promise check and the ETA agree.
        at_counter = planning_service_minutes(
            (await profiles_by_location(session, [shop.location_id])).get(shop.location_id)
            if shop.location_id is not None
            else None
        )

        best: tuple[float, Route, datetime, datetime] | None = None
        for route in active_routes:
            driver_state = await self._fleet_state.get_driver_state(hub_id, str(route.driver_id))
            if driver_state is None:
                continue
            remaining_capacity = max(driver_state.capacity_units - driver_state.load_units, 0.0)
            if remaining_capacity < weight:
                continue

            end = await self._route_end(session, hub_id, route)
            if end is None:
                continue
            end_lat, end_lng, free_at = end
            detour = _miles(end_lat, end_lng, shop.lat, shop.lng)
            if detour is None or detour > radius:
                continue
            at_pickup = free_at + timedelta(minutes=minutes_for_miles(detour))
            at_drop = at_pickup + timedelta(minutes=at_counter + pickup_to_drop)
            if promise is not None and at_drop > promise:
                continue
            if best is None or detour < best[0]:
                best = (detour, route, at_pickup, at_drop)
        return best

    async def _held_orders_a_driver_is_passing(
        self, hub_id: str, held_orders: list[HeldOrder]
    ) -> set[str]:
        """Which held orders a driver on an active route will pass with room and
        time for (§6, question 3). The queue's rules are pure and know nothing
        of routes, so this is worked out here and handed in.

        HOT_SHOT and past-deadline orders are released by earlier questions
        whatever this says, so they aren't looked up.
        """
        candidates = [
            o for o in held_orders
            if o.sla_tier != "HOT_SHOT" and o.hold_deadline > datetime.now(timezone.utc)
        ]
        if not candidates:
            return set()
        passing: set[str] = set()
        async with session_scope() as session:
            active_routes = list(
                (
                    await session.execute(
                        select(Route).where(Route.hub_id == uuid.UUID(hub_id), Route.status == "active")
                    )
                ).scalars().all()
            )
            if not active_routes:
                return passing
            for held in candidates:
                order = await session.get(Order, uuid.UUID(held.order_id))
                if order is None or order.delivery_lat is None or order.delivery_lng is None:
                    continue
                shop = await session.get(Shop, order.shop_id)
                if shop is None:
                    continue
                # Weight 1.0 per order, as the cycle's StopCandidate assumes
                # (HeldOrder carries none).
                if await self._passing_route(session, hub_id, order, shop, 1.0, active_routes):
                    passing.add(held.order_id)
        return passing

    async def _route_end(
        self, session, hub_id: str, route: Route
    ) -> tuple[float, float, datetime] | None:
        """Where this route's driver will be when its last stop is done, and when.

        The last stop by sequence: a pickup is at its shop, a drop-off at its
        order's delivery address. When is its live `eta` - the refreshed one,
        so a route running late is not judged free at the time it was planned
        to be - plus the time the driver will spend at that door, which is the
        dock's observed dwell or the placeholder. A stop never given an ETA
        falls back to its plan, then to now, which understates rather than
        invents. A stop with no address at all falls back to the driver's live
        position, as of now. None when neither is known: a route whose end
        can't be placed is not known to be passing anything.
        """
        last = (
            await session.execute(
                select(Stop)
                .where(Stop.route_id == route.id, Stop.status != "cancelled")
                .order_by(Stop.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if last is not None:
            arrives = last.eta or last.planned_eta or now
            if last.stop_type == "pickup" and last.shop_id is not None:
                shop = await session.get(Shop, last.shop_id)
                if shop is not None and shop.lat is not None and shop.lng is not None:
                    profile = (
                        (await profiles_by_location(session, [shop.location_id])).get(shop.location_id)
                        if shop.location_id is not None
                        else None
                    )
                    free_at = arrives + timedelta(minutes=planning_service_minutes(profile))
                    return float(shop.lat), float(shop.lng), free_at
            else:
                drop = (
                    await session.execute(
                        select(Order.delivery_lat, Order.delivery_lng, Order.delivery_location_id)
                        .join(StopOrder, StopOrder.order_id == Order.id)
                        .where(StopOrder.stop_id == last.id, Order.delivery_lat.is_not(None))
                        .limit(1)
                    )
                ).first()
                if drop is not None:
                    profile = (
                        (await profiles_by_location(session, [drop.delivery_location_id])).get(
                            drop.delivery_location_id
                        )
                        if drop.delivery_location_id is not None
                        else None
                    )
                    free_at = arrives + timedelta(minutes=planning_service_minutes(profile))
                    return float(drop.delivery_lat), float(drop.delivery_lng), free_at
        position = await self._fleet_state.get_driver_location(hub_id, str(route.driver_id))
        if position is None:
            return None
        return position.lat, position.lng, now
