"""Putting back what Redis lost, from Postgres.

Redis holds the dispatch working set: the hold queue (which orders are waiting,
and when their hold runs out) and the fleet state (who is on shift, offered or on
a route, with what capacity and load). Nothing restored either. A flushed or
replaced node came back empty, and then:

  - every held order sat in Postgres as `held` and was never dispatched, because
    every dispatch path reads only the Redis queue - and the liveness check skips a
    hub whose queue is empty, so it reported healthy;
  - every on-shift driver was invisible to dispatch until they toggled off and on,
    and a driver mid-route was never put back in the pool when the route ended.

Postgres holds enough to rebuild both, so this does, per active hub, under the
same hub lock every dispatch cycle takes (`app/events/bus.py`'s `hub_lock`), with
writes that never overwrite a fresher Redis value:

  - **Hold queue.** Every live order in `held` or `queued` that isn't in the queue
    goes back in. Never `assigned`: an in-flight offer's orders are `assigned`, and
    re-queuing them would offer them twice. Never a backfilled order: that would
    send a driver to a delivery that already happened. Entries whose order is
    finished, backfilled or gone are pruned; an `assigned`, `accepted` or
    `delivery_failed` entry is left, because requeue and redelivery write Redis
    before their own commit and pruning would race them (the dispatch cycle skips
    such entries anyway, `app/optimizer/service.py`'s `_PLANNABLE`).
  - **Fleet state.** Each driver with no state gets one derived from Postgres: an
    active route means `en_route` with its load; a live offer means `offered`, so
    they aren't offered a second job; otherwise their own on/off-shift choice.
    Existing states are corrected only where Postgres proves them wrong: `offered`
    with no outstanding offer, and `en_route` on a route that has ended.

**Detecting the loss.** A sentinel key with no expiry is written after a complete
pass. Missing means Redis lost its data (or this is the first boot): logged at
error level as `redis_state_lost`, the event to alert on. Present, the same pass
runs as a drift check. Either way the work is identical, so there is one path.

It runs at startup, on every dispatch sweep, and from a 30-second watch that
catches a flush between sweeps. Running it twice, or on two processes at once, is
wasted work and nothing worse.

**Never restore a Redis snapshot into the live cluster.** A snapshot brings back
hours-old fleet states, which this deliberately does not overwrite. Start empty
and let this rebuild.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.db import session_scope
from app.events.bus import DIRTY_HUBS_KEY, hub_lock
from app.fleet_state.manager import FleetStateManager
from app.models.driver import Driver
from app.models.driver_location_ping import DriverLocationPing
from app.models.hub import Hub
from app.models.order import INTAKE_LIVE, Order, OrderStatus
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.redis_client import get_client
from app.schemas.fleet import DriverLocation, DriverState

logger = structlog.get_logger(__name__)

SENTINEL_KEY = "redis_state:epoch"
LOSS_SEEN_KEY = "redis_state:loss_seen"
WATCH_INTERVAL_SECONDS = 30
_QUEUEABLE = (OrderStatus.held, OrderStatus.queued)
_PRUNABLE = (OrderStatus.delivered, OrderStatus.cancelled, OrderStatus.returned)
# A hold with no deadline on file still has to run out some time.
_DEFAULT_HOLD = timedelta(minutes=5)


def held_order_from(order: Order, shop: Shop, *, held_since: datetime, now: datetime) -> HeldOrder | None:
    """The queue entry for an order, as intake would have written it."""
    if order.sla_tier is None:
        logger.warning("redis_rebuild_order_without_tier", order_id=str(order.id))
        return None
    return HeldOrder(
        order_id=str(order.id),
        shop_lat=float(shop.lat),
        shop_lng=float(shop.lng),
        sla_tier=order.sla_tier.value,
        hold_deadline=order.hold_deadline or now + _DEFAULT_HOLD,
        held_since=held_since,
        shop_name=shop.name,
        delivery_lat=float(order.delivery_lat) if order.delivery_lat is not None else None,
        delivery_lng=float(order.delivery_lng) if order.delivery_lng is not None else None,
    )


async def reconcile_hold_queue(session: AsyncSession, hub_id: str, now: datetime) -> dict[str, int]:
    store = HoldQueueStore()
    queued_ids = await store.order_ids(hub_id)

    rows = await session.execute(
        select(Order, Shop)
        .join(Shop, Shop.id == Order.shop_id)
        .where(
            Order.hub_id == uuid.UUID(hub_id),
            Order.status.in_(_QUEUEABLE),
            Order.intake_mode == INTAKE_LIVE,
        )
    )
    added = 0
    for order, shop in rows.all():
        if str(order.id) in queued_ids:
            continue
        held = held_order_from(order, shop, held_since=order.updated_at or now, now=now)
        if held is not None and await store.add_if_absent(hub_id, held):
            added += 1

    pruned = 0
    known: dict[str, tuple[OrderStatus, str]] = {}
    parseable = []
    for order_id in queued_ids:
        try:
            parseable.append(uuid.UUID(order_id))
        except ValueError:
            continue  # not an order id at all; nothing here can judge it
    if parseable:
        status_rows = await session.execute(
            select(Order.id, Order.status, Order.intake_mode).where(Order.id.in_(parseable))
        )
        known = {str(i): (status, mode) for i, status, mode in status_rows.all()}
    for parsed in parseable:
        entry = known.get(str(parsed))
        if entry is None or entry[0] in _PRUNABLE or entry[1] != INTAKE_LIVE:
            await store.remove(hub_id, str(parsed))
            pruned += 1

    if added:
        # Look at the hub now rather than at the next event.
        await get_client().sadd(DIRTY_HUBS_KEY, hub_id)
    return {"orders_added": added, "orders_pruned": pruned}


async def _route_loads(session: AsyncSession, route_ids: list[uuid.UUID]) -> dict[uuid.UUID, float]:
    """What each active route is carrying: collected and not yet dropped or failed."""
    if not route_ids:
        return {}
    collected = await session.execute(
        select(Stop.route_id, StopOrder.order_id)
        .join(StopOrder, StopOrder.stop_id == Stop.id)
        .where(Stop.route_id.in_(route_ids), Stop.stop_type == "pickup", Stop.status == "completed")
    )
    finished = await session.execute(
        select(Stop.route_id, StopOrder.order_id)
        .join(StopOrder, StopOrder.stop_id == Stop.id)
        .where(
            Stop.route_id.in_(route_ids),
            Stop.stop_type == "dropoff",
            Stop.status.in_(("completed", "failed")),
        )
    )
    done = set(finished.all())
    carried: dict[uuid.UUID, list[uuid.UUID]] = {}
    for route_id, order_id in collected.all():
        if (route_id, order_id) not in done:
            carried.setdefault(route_id, []).append(order_id)
    order_ids = [o for orders in carried.values() for o in orders]
    if not order_ids:
        return {}
    weights = dict(
        (await session.execute(select(Order.id, Order.weight_units).where(Order.id.in_(order_ids)))).all()
    )
    return {route_id: float(sum(weights.get(o, 0) for o in orders)) for route_id, orders in carried.items()}


async def reconcile_fleet_state(session: AsyncSession, hub_id: str, now: datetime) -> dict[str, int]:
    hub_uuid = uuid.UUID(hub_id)
    drivers = (await session.scalars(select(Driver).where(Driver.hub_id == hub_uuid))).all()
    if not drivers:
        return {"drivers_seeded": 0, "drivers_corrected": 0}
    driver_ids = [d.id for d in drivers]

    active_routes = {
        driver_id: route_id
        for driver_id, route_id in (
            await session.execute(
                select(Route.driver_id, Route.id)
                .where(Route.driver_id.in_(driver_ids), Route.status == "active")
                .order_by(Route.created_at)
            )
        ).all()
    }
    open_offers = (
        await session.execute(
            select(RouteOffer.driver_id, RouteOffer.expires_at).where(
                RouteOffer.driver_id.in_(driver_ids), RouteOffer.status == "offered"
            )
        )
    ).all()
    any_open_offer = {driver_id for driver_id, _ in open_offers}
    loads = await _route_loads(session, list(active_routes.values()))

    manager = FleetStateManager()
    # Positions, from the ping log. Dispatch won't offer work to a driver with no
    # position, so without these a rebuilt driver waited for their next ping; a
    # position older than the optimizer would use anyway isn't worth restoring.
    fresh_after = now - timedelta(seconds=settings.driver_position_stale_after_seconds)
    recent_pings = (
        await session.execute(
            select(DriverLocationPing)
            .where(DriverLocationPing.driver_id.in_(driver_ids), DriverLocationPing.recorded_at > fresh_after)
            .order_by(DriverLocationPing.recorded_at.desc())
        )
    ).scalars().all()
    latest: dict[uuid.UUID, DriverLocationPing] = {}
    for ping in recent_pings:  # newest first, so the first per driver wins
        latest.setdefault(ping.driver_id, ping)
    for ping in latest.values():
        await manager.seed_location_if_absent(
            DriverLocation(
                driver_id=str(ping.driver_id), lat=ping.lat, lng=ping.lng, recorded_at=ping.recorded_at.isoformat()
            ),
            hub_id,
        )

    seeded = corrected = 0
    for driver in drivers:
        route_id = active_routes.get(driver.id)
        if not driver.is_active:
            status, current_route = "off_shift", None
        elif route_id is not None:
            status, current_route = "en_route", route_id
        # Any offer still marked offered, lapsed or not: a lapsed one is expired
        # (and the driver freed) by the next sweep or their own app, and seeding
        # them available first would let a cycle offer them a second job.
        elif driver.id in any_open_offer and driver.status == "available":
            status, current_route = "offered", None
        elif driver.status in ("available", "on_break", "off_shift"):
            status, current_route = driver.status, None
        else:
            status, current_route = "available", None
        state = DriverState(
            driver_id=str(driver.id),
            hub_id=hub_id,
            status=status,
            capacity_units=driver.vehicle_capacity_units,
            load_units=loads.get(route_id, 0.0) if route_id is not None else 0.0,
            current_route_id=str(current_route) if current_route else None,
        )
        if await manager.seed_driver_state_if_absent(state):
            seeded += 1
            continue

        if not driver.is_active:
            continue
        existing = await manager.get_driver_state(hub_id, str(driver.id))
        if existing is None:
            continue
        # Back to the driver's own choice, not a blanket `available`: one who went
        # on a break mid-route stays on it.
        own_choice = driver.status if driver.status in ("available", "on_break", "off_shift") else "available"
        # Offered, with no offer outstanding: a cycle that set `offered` and then
        # failed to commit its offer, or one whose offer Redis forgot was answered.
        if existing.status == "offered" and driver.id not in any_open_offer:
            if await manager.correct_status_if(
                hub_id, str(driver.id), expect_status="offered", new_status=own_choice
            ):
                corrected += 1
        # On a route that has ended. A route id with no row at all is left: that
        # is an accept whose commit hasn't landed yet.
        elif existing.status == "en_route" and existing.current_route_id:
            route = await session.get(Route, uuid.UUID(existing.current_route_id))
            if route is not None and route.status != "active":
                if await manager.correct_status_if(
                    hub_id, str(driver.id), expect_status="en_route", new_status=own_choice
                ):
                    corrected += 1
    return {"drivers_seeded": seeded, "drivers_corrected": corrected}


async def reconcile_redis_state(now: datetime | None = None) -> dict:
    """Rebuild every active hub's hold queue and fleet state from Postgres."""
    now = now or datetime.now(timezone.utc)
    redis = get_client()
    lost = not await redis.exists(SENTINEL_KEY)
    # Logged once per loss, not on every pass until a pass completes: a hub
    # that keeps failing would otherwise repeat the alert every 30 seconds.
    if lost and await redis.set(LOSS_SEEN_KEY, "1", nx=True):
        logger.error(
            "redis_state_lost",
            detail="no rebuild sentinel - Redis was flushed, replaced, or this is a first boot; rebuilding from Postgres",
        )

    async with session_scope() as session:
        hub_ids = [str(h) for h in (await session.scalars(select(Hub.id).where(Hub.active.is_(True)))).all()]

    hubs: dict[str, dict | str] = {}
    complete = True
    for hub_id in hub_ids:
        try:
            async with hub_lock(hub_id) as acquired:
                if not acquired:
                    hubs[hub_id] = "skipped: a cycle is running"
                    complete = False
                    continue
                async with session_scope() as session:
                    hubs[hub_id] = {
                        **await reconcile_hold_queue(session, hub_id, now),
                        **await reconcile_fleet_state(session, hub_id, now),
                    }
        except Exception as exc:  # noqa: BLE001 - one hub must not stop the rest
            logger.exception("redis_rebuild_hub_failed", hub_id=hub_id)
            hubs[hub_id] = f"error: {type(exc).__name__}"
            complete = False

    if complete:
        await redis.set(SENTINEL_KEY, json.dumps({"rebuilt_at": now.isoformat()}))
        await redis.delete(LOSS_SEEN_KEY)
    logger.info("redis_rebuild_complete", lost=lost, complete=complete, hubs=hubs)
    return {"lost": lost, "complete": complete, "hubs": hubs}


async def watch_redis_state() -> None:
    """Rebuild within seconds of a flush, not at the next sweep.

    One EXISTS every 30 seconds. The sweep alone left a gap of up to five
    minutes, and none at all before the HTTPS certificate exists, because the
    scheduled sweep isn't armed until then.
    """
    while True:
        await asyncio.sleep(WATCH_INTERVAL_SECONDS)
        try:
            if not await get_client().exists(SENTINEL_KEY):
                await reconcile_redis_state()
        except Exception:
            logger.exception("redis_state_watch_failed")
