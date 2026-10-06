"""Per-stop arrival times for a route.

**`Stop.eta` was read in two places and written in none.** The driver app's route
payload carries an `eta` for every stop and the field was structurally always null; the
model docstring describes `arrived_at` vs `eta` as I1's direct ETA-accuracy ground truth,
and one side of that comparison never existed. This module writes it.

Three decisions worth knowing before reading the code.

**It walks the accepted sequence, not the solver's planned timestamps.** The obvious
source of arrival times is the optimizer - `optimizeTours` is called with
`considerRoadTraffic` and returns a `startTime` per visit, and since L22 those times are
carried all the way onto the offer (`RouteOffer.visit_payload`). They are still not used
as ETAs directly, for two remaining reasons rather than the original one:

  - **They are absolute and perishable.** The plan assumes the route starts when it was
    made, and an offer can sit unaccepted for `job_offer_ttl_seconds`. Writing them
    verbatim would quote arrival times computed from a departure that never happened.
  - **HOT_SHOT legs are still hoisted** in `accept_offer`, so one override of the plan
    survives and any stop after it has moved. Since L23 the solver does receive the
    collection deadline as a soft time window, so the hoist is now waiting on evidence
    rather than on a missing constraint - `scripts/verify_route_optimization.py` check 5
    is what retires it.

What is now available and worth having is the *intervals* between planned visits: real
road-network travel times, which is exactly what `minutes_for_miles` approximates at an
assumed speed. Shifting the walk onto those is the next step, and it is bounded by
sending `timeWindows` to the solver so the HOT_SHOT hoist can go away.

**One travel model, shared.** `minutes_for_miles` from `app/travel.py` for the legs -
the same placeholder the accept-gate, the client portal's estimate and the recipient
tracking page use. The point is not that the model is good; it is that a driver, a
recipient and a counter person must never be shown numbers derived three different ways.

**Time at the door is the dock's own, where we know it.** The nightly dwell refresh
(`IDN-4`) stores each dock's median dwell on its `ReceiverProfile`, beside any figure
inherited from a previous operator's export; the walk reads them through
`planning_service_minutes`, which falls back to the flat
`PLACEHOLDER_STOP_SERVICE_MINUTES` for a dock with no figure or one over fewer than
five visits (its docstring has the full rule). A pickup's dock is its shop's; a
drop-off's is the order's delivery door (since #182). Most docks start on the flat
figure, so the ETAs tighten dock by dock rather than all at once. The in-flight
insertion uses the same function for the same reason.

**A missing location ends the walk.** If a stop has no coordinates, it gets no ETA - and
neither does anything after it, because you cannot know when a driver reaches stop 5
without knowing where stop 3 is. Refusing is the same convention the rest of this
codebase uses for an answer it cannot compute.

`planned_eta` is written once, at acceptance, and never updated. `eta` is refreshed as
the route progresses. Both exist because they answer different questions: the driver
wants to know when they will get there now, and I1 wants to know how good the prediction
was when it was made. Refreshing a single column right up until arrival would have left
`arrived_at - eta` measuring the last few minutes of a route and calling it ETA accuracy.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.clustering import miles_between
from app.identity.inherited_dwell import planning_service_minutes, profiles_by_location
from app.travel import minutes_for_miles
from app.models.driver_location_ping import DriverLocationPing
from app.models.hub import Hub
from app.models.order import Order
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder

logger = structlog.get_logger(__name__)

# A stop the driver has already got to. Its ETA stops being a prediction the moment they
# arrive, so it is frozen there rather than at completion - recomputing it would replace a
# forecast with a description of the present, which is both useless and a quiet way to
# destroy the accuracy signal. `arrived` counts even though the driver is still working
# the stop: the question "when will you get here" has been answered.
#
# `cancelled` is here too, for a different reason: nobody is going there. Dispatch
# cancelling an order (#187) marks its stops rather than deleting them, and a walk that
# still drove to a cancelled stop and waited there added its leg and its dwell to every
# ETA after it, for the rest of the route.
_REACHED = ("arrived", "completed", "failed", "cancelled")


@dataclass(frozen=True)
class _Point:
    """A stop reduced to what an ETA walk needs."""

    stop_id: uuid.UUID
    sequence: int
    status: str
    lat: float | None
    lng: float | None
    arrived_at: datetime | None
    completed_at: datetime | None
    # Minutes on the ground here: the dock's observed median, or the placeholder.
    service_minutes: float

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lng is not None

    @property
    def reached(self) -> bool:
        return self.status in _REACHED

    @property
    def observed_at(self) -> datetime | None:
        """The latest thing we actually know happened here.

        A completion beats an arrival: a driver who has left is a better anchor for the
        next leg than one who was standing at the door twenty minutes ago.
        """
        return self.completed_at or self.arrived_at


async def _points(session: AsyncSession, route_id: uuid.UUID) -> list[_Point]:
    """Every stop on the route, in sequence, with coordinates resolved.

    A pickup's location is its shop's. A dropoff has no coordinates of its own - they
    live on the order being delivered, reached through `StopOrder`. That asymmetry is
    also why `driver_routes.py` builds its stop views in two branches.
    """
    stops = (
        (
            await session.execute(
                select(Stop).where(Stop.route_id == route_id).order_by(Stop.sequence)
            )
        )
        .scalars()
        .all()
    )
    if not stops:
        return []

    stop_ids = [s.id for s in stops]
    links = (
        await session.execute(select(StopOrder).where(StopOrder.stop_id.in_(stop_ids)))
    ).scalars().all()
    orders_by_stop: dict[uuid.UUID, uuid.UUID] = {}
    for link in links:
        orders_by_stop.setdefault(link.stop_id, link.order_id)

    shop_ids = {s.shop_id for s in stops if s.shop_id}
    shops = (
        (await session.execute(select(Shop).where(Shop.id.in_(shop_ids)))).scalars().all()
        if shop_ids
        else []
    )
    shops_by_id = {s.id: s for s in shops}

    order_ids = set(orders_by_stop.values())
    orders = (
        (await session.execute(select(Order).where(Order.id.in_(order_ids)))).scalars().all()
        if order_ids
        else []
    )
    orders_by_id = {o.id: o for o in orders}

    # Each stop's dock, for its dwell figure: a pickup's is its shop's, a drop-off's
    # is the order's delivery door. Either may be unknown, and then the placeholder
    # answers.
    profiles = await profiles_by_location(
        session,
        [s.location_id for s in shops] + [o.delivery_location_id for o in orders],
    )

    points: list[_Point] = []
    for stop in stops:
        lat = lng = None
        location_id = None
        if stop.stop_type == "pickup":
            shop = shops_by_id.get(stop.shop_id) if stop.shop_id else None
            if shop is not None:
                lat, lng = shop.lat, shop.lng
                location_id = shop.location_id
        else:
            order_id = orders_by_stop.get(stop.id)
            order = orders_by_id.get(order_id) if order_id is not None else None
            if order is not None and order.delivery_lat is not None and order.delivery_lng is not None:
                lat, lng = float(order.delivery_lat), float(order.delivery_lng)
            if order is not None:
                location_id = order.delivery_location_id
        points.append(
            _Point(
                stop_id=stop.id,
                sequence=stop.sequence,
                status=stop.status,
                lat=lat,
                lng=lng,
                arrived_at=stop.arrived_at,
                completed_at=stop.completed_at,
                service_minutes=planning_service_minutes(
                    profiles.get(location_id) if location_id is not None else None
                ),
            )
        )
    return points


async def _anchor(
    session: AsyncSession,
    route: Route,
    points: list[_Point],
    now: datetime,
) -> tuple[float, float, datetime] | None:
    """Where the driver is, and as of when. The walk starts here.

    Three sources, most-recently-observed first:

      - the latest location ping, which is the only one that knows about a driver
        currently sitting in traffic between two stops;
      - the last stop they reached, for a driver whose app has not pinged (offline, or
        a route accepted seconds ago);
      - the hub, for a route where nothing has happened yet.

    Returns None when there is nothing to anchor to, which means no ETAs rather than
    ETAs measured from a guess.
    """
    ping = (
        await session.execute(
            select(DriverLocationPing)
            .where(DriverLocationPing.driver_id == route.driver_id)
            .order_by(DriverLocationPing.recorded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    reached = [p for p in points if p.reached and p.observed_at and p.located]
    last_reached = reached[-1] if reached else None

    candidates: list[tuple[float, float, datetime]] = []
    if ping is not None:
        candidates.append((ping.lat, ping.lng, ping.recorded_at))
    if last_reached is not None:
        # `reached` holds only located, observed points, so none of these is None -
        # spelled out because the type checker cannot see through `located`.
        seen_lat, seen_lng, seen_at = last_reached.lat, last_reached.lng, last_reached.observed_at
        if seen_lat is not None and seen_lng is not None and seen_at is not None:
            candidates.append((seen_lat, seen_lng, seen_at))

    if candidates:
        return max(candidates, key=lambda c: c[2])

    hub = await session.get(Hub, route.hub_id)
    if hub is None:
        return None
    return (hub.lat, hub.lng, now)


async def refresh_route_etas(
    session: AsyncSession, route_id: uuid.UUID, *, now: datetime | None = None
) -> dict:
    """Recompute `Stop.eta` for every stop the driver has not yet reached.

    Idempotent and cheap enough to call on every driver transition. Does not commit -
    the caller owns the transaction, which matters because this runs inside
    `accept_offer` and `complete_stop` alongside changes that must land together.

    Returns a small summary rather than nothing, so a caller (or a test) can tell
    "recomputed six ETAs" from "computed none because a stop has no address".
    """
    reference = now or datetime.now(timezone.utc)

    route = await session.get(Route, route_id)
    if route is None:
        return {"written": 0, "reason": "no_route"}

    points = await _points(session, route_id)
    if not points:
        return {"written": 0, "reason": "no_stops"}

    anchor = await _anchor(session, route, points, reference)
    if anchor is None:
        logger.info("route_eta_no_anchor", route_id=str(route_id))
        return {"written": 0, "reason": "no_anchor"}

    lat, lng, at = anchor
    # Never predict into the past. A stale ping or an old completion would otherwise
    # produce an ETA that has already been and gone.
    cursor = max(at, reference)

    # A driver standing at a door is not leaving it yet. With the stop they have
    # arrived at frozen and skipped below, the next leg would otherwise start the
    # moment they arrived - invisible at eight minutes a stop, a real error at a
    # counter that takes twenty. They leave when the dwell runs out, or now if it
    # already has.
    #
    # The door they are at is the last thing they did: the reached stop observed
    # most recently, if that was an arrival not yet completed. Nothing stops an
    # earlier stop being left on `arrived` - a driver who drove on without
    # completing it - and that stop says nothing about where they are now.
    seen = [(p.observed_at, p) for p in points if p.reached and p.observed_at is not None]
    if seen:
        _, current = max(seen, key=lambda pair: pair[0])
        if current.status == "arrived" and current.completed_at is None and current.arrived_at is not None:
            cursor = max(cursor, current.arrived_at + timedelta(minutes=current.service_minutes))

    written = 0
    stalled: str | None = None
    for point in points:
        if point.reached:
            continue
        point_lat, point_lng = point.lat, point.lng
        # `point.located`, spelled out so the coordinates below are known to exist.
        if point_lat is None or point_lng is None:
            # This stop and everything after it. Recorded once with the sequence, so a
            # missing address is diagnosable rather than just an absent number.
            stalled = f"stop_{point.sequence}_unlocated"
            break

        cursor = cursor + timedelta(
            minutes=minutes_for_miles(miles_between(lat, lng, point_lat, point_lng))
        )
        stop = await session.get(Stop, point.stop_id)
        if stop is not None:
            stop.eta = cursor
            # Written once, at the first computation for this stop, and never again.
            if stop.planned_eta is None:
                stop.planned_eta = cursor
            written += 1

        # Time on the ground before the next leg starts. The ETA itself is arrival, so
        # the dwell is added after it rather than before.
        cursor = cursor + timedelta(minutes=point.service_minutes)
        lat, lng = point_lat, point_lng

    if stalled:
        logger.info("route_eta_incomplete", route_id=str(route_id), reason=stalled, written=written)

    return {"written": written, "reason": stalled or "ok"}
