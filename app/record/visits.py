"""Technician visits: a re-drive day of sites with no delivery (`DE-6`).

`docs/ROADMAP_1.5.md` DE-6 and §2.5 D-VISIT. A re-drive sends a technician back
to sites we have delivered to, to capture each one's door - the handoff point
the modality model learns from - without carrying anything. Each site is a
`visit` stop: no order, no proof of delivery, no parcels. The driver app runs
the same golden capture as a drop-off (`DE-4`: pins, the handoff questions, four
photos), and access - who receives, and whether a booking is needed - is
recorded as unknown, because somebody who hands nothing over cannot learn it.

**Fenced like the Experiment 0 import (`DE-3`).** The day's route sits in one
inactive hub per region, so nothing that runs the business reads it: dispatch's
cycle, its in-flight insertion and the sweeps all work hub by hub over active
hubs, and there are no orders for billing, payroll's per-delivery pay or a
client to see. The technician is a real, active driver - the app shows them the
route as it would any other - and is refused a day while they hold other work,
since the app shows one route at a time. Each visit's `record_source` is
`redrive`, so the held-wrong rate, which counts `lmx_app` stops, never reads it.

**Graded on its door (D-VISIT).** `record_tiers` (migration 0077) grades a visit
on the drop-off end and the autonomy qualifier only: both is gold, the end
alone silver, no end reference.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.handoff_capture import handoff_point_for
from app.identity.resolution import canonical_location
from app.models.driver import Driver
from app.models.handoff_point import HandoffPoint
from app.models.hub import Hub
from app.models.location import Location
from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.stop import Stop

SOURCE_REDRIVE = "redrive"
# A day, not a campaign: enough for a long shift of short stops.
MAX_VISITS_PER_DAY = 60


class VisitDayRefused(ValueError):
    """The day cannot be planned as asked; the message says why."""


@dataclass
class VisitDay:
    route_id: uuid.UUID
    hub_id: uuid.UUID
    stop_ids: list[uuid.UUID]


def redrive_hub_name(region: str) -> str:
    return f"Re-drive visits ({region})"


async def redrive_hub(session: AsyncSession, region: str, *, timezone: str) -> Hub:
    """The region's re-drive hub, made on first use. Inactive, and at no
    coordinates: it is a container for visit routes, not a depot."""
    hub = (
        await session.scalars(
            select(Hub)
            .where(Hub.name == redrive_hub_name(region), Hub.active.is_(False))
            .order_by(Hub.created_at)
            .limit(1)
        )
    ).first()
    if hub is None:
        hub = Hub(name=redrive_hub_name(region), timezone=timezone, lat=0.0, lng=0.0, active=False)
        session.add(hub)
        await session.flush()
    return hub


async def sites_to_visit(session: AsyncSession, region: str, limit: int) -> list[uuid.UUID]:
    """Delivered-to sites in a region that no visit has been planned for, the
    most delivered-to first.

    A site counts as delivered to when a drop-off used its handoff point - which
    is how the Experiment 0 import links every record to its site - so a
    supplier's counter, seen only as a pickup, is never sent a technician.
    """
    deliveries = (
        select(HandoffPoint.location_id, func.count(Stop.id).label("deliveries"))
        .join(Stop, Stop.handoff_point_id == HandoffPoint.id)
        .where(Stop.stop_type == "dropoff")
        .group_by(HandoffPoint.location_id)
        .subquery()
    )
    visited = (
        select(HandoffPoint.location_id)
        .join(Stop, Stop.handoff_point_id == HandoffPoint.id)
        .where(Stop.stop_type == "visit")
    )
    rows = await session.execute(
        select(Location.id)
        .join(deliveries, deliveries.c.location_id == Location.id)
        .where(
            Location.region == region,
            Location.merged_into_id.is_(None),
            Location.id.notin_(visited),
        )
        .order_by(deliveries.c.deliveries.desc(), Location.id)
        .limit(limit)
    )
    return [row[0] for row in rows.all()]


async def plan_visit_day(
    session: AsyncSession,
    *,
    driver_id: uuid.UUID,
    region: str,
    location_ids: list[uuid.UUID],
) -> VisitDay:
    """One active route of visits, in the order given, for one technician.

    Every site must be in `region`; a merged site is visited as the place it was
    merged into. Flushes; the caller commits, and then puts the driver on the
    route in dispatch's fleet state, as accepting an offer does.
    """
    if not location_ids:
        raise VisitDayRefused("no sites to visit")
    if len(location_ids) > MAX_VISITS_PER_DAY:
        raise VisitDayRefused(f"{len(location_ids)} sites is more than a day ({MAX_VISITS_PER_DAY})")
    if len(set(location_ids)) != len(location_ids):
        raise VisitDayRefused("a site is listed twice")

    # Locked, as switching a driver off locks it: a dispatch cycle offering this
    # driver work either lands first, and is refused below, or waits.
    driver = (
        await session.execute(select(Driver).where(Driver.id == driver_id).with_for_update())
    ).scalar_one_or_none()
    if driver is None:
        raise VisitDayRefused("no such driver")
    if not driver.is_active:
        raise VisitDayRefused("this driver is switched off")
    open_routes = await session.scalar(
        select(func.count())
        .select_from(Route)
        .where(Route.driver_id == driver.id, Route.status.in_(("planned", "active")))
    )
    pending_offers = await session.scalar(
        select(func.count())
        .select_from(RouteOffer)
        .where(RouteOffer.driver_id == driver.id, RouteOffer.status == "offered")
    )
    if open_routes or pending_offers:
        raise VisitDayRefused("this driver holds a route or an unanswered offer; plan the day once it is done")

    sites: list[Location] = []
    for location_id in location_ids:
        location = await session.get(Location, location_id)
        if location is None:
            raise VisitDayRefused(f"no site {location_id}")
        location = await canonical_location(session, location)
        if location.region != region:
            raise VisitDayRefused(f"site {location_id} is not in {region}")
        sites.append(location)
    if len({site.id for site in sites}) != len(sites):
        raise VisitDayRefused("two of the sites are the same place")

    home = await session.get_one(Hub, driver.hub_id)
    hub = await redrive_hub(session, region, timezone=home.timezone)
    route = Route(hub_id=hub.id, driver_id=driver.id, status="active", plan_version=1)
    session.add(route)
    await session.flush()

    stop_ids: list[uuid.UUID] = []
    for sequence, site in enumerate(sites, start=1):
        point = await handoff_point_for(session, site, create=True)
        stop = Stop(
            route_id=route.id,
            stop_type="visit",
            sequence=sequence,
            status="pending",
            parcel_count=0,
            handoff_point_id=point.id if point is not None else None,
            record_source=SOURCE_REDRIVE,
            collected_by=str(driver.id),
        )
        session.add(stop)
        await session.flush()
        stop_ids.append(stop.id)
    return VisitDay(route_id=route.id, hub_id=hub.id, stop_ids=stop_ids)

