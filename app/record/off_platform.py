"""Off-platform records: a gig job logged as a golden record (`DE-7`).

`docs/ROADMAP_1.5.md` DE-7. A driver delivering a job from Curri, Dispatch or
Roadie stands at the same counters and doors an LMX delivery does, so the job is
captured the way an LMX delivery is: the item at the counter (`DE-5`'s fields,
plus what it is), the door at the drop-off (`DE-4`'s pins, questions and
photos), the pickup counter if there is time. This is data collection only -
the gig *demand* path is retired (`docs/ROADMAP_RECONCILIATION.md`), and nothing
here dispatches, prices or pays anything.

**One record per job, shaped like an LMX delivery**, so everything that reads
records reads it unchanged: `record_tiers`, the labelling queue, the export and
the scorecard all start from a drop-off stop. Logging a job makes one stand-in
order holding the item, a pickup stop and a drop-off stop, each with
`record_source = 'off_platform'`, and the job points at the order
(`gig_jobs.record_order_id`). The drop-off stop *is* the record: its id, chosen
by the phone, is the record's id, so the door's survey and photos can be queued
behind it before there is any signal (the outbox sends one stop's actions in
order).

**Fenced like the Experiment 0 import (`DE-3`) and the re-drive (`DE-6`)**, and
further, because these stops carry an order:

  - The order and route sit in one inactive hub per region, which dispatch, its
    sweeps and the hub-scoped console never read.
  - The order has no client, so no client-scoped read - the portal, invoices,
    statements, credits - can reach it, and is `intake_mode = 'backfill'`, the
    mode billing and the hold-queue rebuild already exclude.
  - The route's status is `record` and the stops' `recorded`: never `active` or
    `completed`, so the driver's route, trip history and trip count, the
    deliveries-per-hour figures and the dwell statistics, which all count
    completed work, never see them; and `recorded` is terminal, so arrive,
    complete, flag and the rest refuse them. Completing a stop is what pays a
    gig-classified driver (`GigPayout`), so a logged job can never be paid that
    way.

**The bonus** for logging a job is recorded on the job once the delivery is - a
drop-off time, not just a record started (`PLACEHOLDER_RECORD_BONUS_CENTS`) - and
not paid: Stripe Connect is stubbed, and a payout keyed on a gig job does not
exist yet. A job typed on the record form rather than picked from the driver's
jobs is recorded with `intake_source = 'record_form'`, which the console's gig
panel shows as one to check before anything is paid: it rests on the driver's
word alone.

**Urgency and the pickup end** are graded the off-platform way (migration 0079):
the platform's drop-off deadline is the job's urgency, since LMX never held it to
set a tier, and the pickup counter is located by its site or the driver's pin
there, since a platform job has no shop.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.gig_platform import service as gig_store
from app.gig_platform.service import DuplicateGigJob
from app.identity.handoff_capture import handoff_point_for
from app.identity.resolution import resolve_location
from app.models.gig_job import GigJob
from app.models.hub import Hub
from app.models.location import Location
from app.models.order import INTAKE_BACKFILL, SOURCE_SYSTEM_OFF_PLATFORM, Order, OrderStatus
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.schemas.off_platform import OffPlatformEnd, OffPlatformRecordBody

SOURCE_OFF_PLATFORM = SOURCE_SYSTEM_OFF_PLATFORM
# A container for one logged job's two stops. Never planned, active or completed,
# so nothing that reads a driver's routes by those statuses reads it.
ROUTE_STATUS_RECORD = "record"
# What a logged stop is: something that happened elsewhere, written down. Terminal
# (app/api/driver_routes.py), and not `completed`, which counts as LMX work.
STOP_STATUS_RECORDED = "recorded"

# What logging one job earns the driver. A placeholder, as `PLACEHOLDER_HOURLY_RATE_CENTS`
# is: the amount is a commercial call nobody has made, and it is owed, not paid.
PLACEHOLDER_RECORD_BONUS_CENTS = 500

# A job typed on the record form, not captured anywhere else: the driver's word
# alone, so the console shows it as one to check before the bonus is paid.
INTAKE_RECORD_FORM = "record_form"

# How far ahead of our clock a phone may say something happened. The same bound
# geofence crossings get: phones drift by seconds, not minutes.
_MAX_DEVICE_CLOCK_LEAD = timedelta(minutes=5)


class RecordNotFound(LookupError):
    """No such job for this driver."""


class RecordConflict(ValueError):
    """The request contradicts what is already recorded."""


class RecordInvalid(ValueError):
    """The request cannot make a record as sent."""


@dataclass
class OffPlatformRecord:
    job: GigJob
    order: Order
    pickup: Stop
    dropoff: Stop


def off_platform_hub_name(region: str | None) -> str:
    return f"Off-platform records ({region})" if region else "Off-platform records"


async def off_platform_hub(session: AsyncSession, region: str | None, *, timezone: str) -> Hub:
    """The region's off-platform hub, made on first use. Inactive, and at no
    coordinates: a container for logged jobs, not a depot.

    Made under a transaction lock on its name, so two first records in a region
    at once make one hub rather than two."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": off_platform_hub_name(region)}
    )
    hub = (
        await session.scalars(
            select(Hub)
            .where(Hub.name == off_platform_hub_name(region), Hub.active.is_(False))
            .order_by(Hub.created_at)
            .limit(1)
        )
    ).first()
    if hub is None:
        hub = Hub(name=off_platform_hub_name(region), timezone=timezone, lat=0.0, lng=0.0, active=False)
        session.add(hub)
        await session.flush()
    return hub


async def _the_job(session: AsyncSession, body: OffPlatformRecordBody, *, driver_id: uuid.UUID, home: Hub) -> GigJob:
    """The job this record is of, locked: this driver's, found by id or by its
    platform reference, or recorded from the form."""
    job_id = body.gig_job_id
    if job_id is None:
        assert body.job is not None  # the body's validator
        existing = await gig_store.find_by_platform_ref(session, body.job.source_platform, body.job.platform_job_ref)
        if existing is None:
            # Typed on the form: marked as such, so it is checked before any
            # bonus on it is paid.
            typed = body.job.model_copy(update={"intake_source": INTAKE_RECORD_FORM})
            try:
                async with session.begin_nested():
                    created = await gig_store.record_job(
                        session, typed, hub_id=str(home.id), driver_id=str(driver_id),
                        status="accepted", commit=False,
                    )
                job_id = created.id
            except DuplicateGigJob as recorded:
                # Recorded in between our lookup and the store's own.
                existing = recorded.existing
            except IntegrityError:
                # The same job recorded by another request in between - a retry
                # racing its own first attempt. Theirs is the row.
                existing = await gig_store.find_by_platform_ref(
                    session, body.job.source_platform, body.job.platform_job_ref
                )
        if job_id is None:
            if existing is None:
                raise RecordConflict("that job could not be recorded; try again")
            job_id = existing.id
    job = (
        await session.execute(select(GigJob).where(GigJob.id == job_id).with_for_update())
    ).scalar_one_or_none()
    # Someone else's job reads as no job, as the gig-job endpoints do.
    if job is None or job.driver_id != driver_id:
        raise RecordNotFound("gig job not found")
    if job.status in ("declined", "cancelled"):
        raise RecordConflict(f"this job is {job.status}, so it was not delivered")
    return job


async def _existing(session: AsyncSession, job: GigJob, record_id: uuid.UUID) -> OffPlatformRecord | None:
    if job.record_order_id is None:
        return None
    order = await session.get_one(Order, job.record_order_id)
    stops = {
        stop.stop_type: stop
        for stop in await session.scalars(
            select(Stop).join(StopOrder, StopOrder.stop_id == Stop.id).where(StopOrder.order_id == order.id)
        )
    }
    dropoff, pickup = stops["dropoff"], stops["pickup"]
    if dropoff.id != record_id:
        raise RecordConflict("this job is already logged as another record")
    return OffPlatformRecord(job=job, order=order, pickup=pickup, dropoff=dropoff)


async def _site(session: AsyncSession, address: str, lat, lng, region: str | None) -> Location:
    location = await resolve_location(
        session, address=address,
        lat=float(lat) if lat is not None else None, lng=float(lng) if lng is not None else None,
    )
    # Whose test pool it may count toward. Only filled where nothing has said, and
    # from where the driver works: a job is delivered near its driver's hub.
    if location.region is None and region is not None:
        location.region = region
    return location


async def _create(
    session: AsyncSession,
    job: GigJob,
    *,
    record_id: uuid.UUID,
    pickup_stop_id: uuid.UUID,
    driver_id: uuid.UUID,
    home: Hub,
) -> OffPlatformRecord:
    for stop_id in (record_id, pickup_stop_id):
        if await session.get(Stop, stop_id) is not None:
            raise RecordConflict("that record id is already taken")
    if record_id == pickup_stop_id:
        raise RecordInvalid("the pickup and the drop-off need ids of their own")
    if not job.dropoff_address:
        raise RecordInvalid("the job needs its drop-off address to be a record")

    region = home.state_code
    try:
        door_site = await _site(session, job.dropoff_address, job.dropoff_lat, job.dropoff_lng, region)
    except ValueError as no_place:
        raise RecordInvalid("the drop-off address does not name a place") from no_place
    try:
        counter_site: Location | None = await _site(
            session, job.pickup_address, job.pickup_lat, job.pickup_lng, region
        )
    except ValueError:
        # A pickup with no real address still has a door worth recording.
        counter_site = None

    hub = await off_platform_hub(session, region, timezone=home.timezone)
    route = Route(hub_id=hub.id, driver_id=driver_id, status=ROUTE_STATUS_RECORD, plan_version=1)
    session.add(route)
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub.id,
        client_id=None,
        shop_id=None,
        external_order_ref=f"{job.source_platform}:{job.platform_job_ref}",
        source_system=SOURCE_OFF_PLATFORM,
        intake_mode=INTAKE_BACKFILL,
        raw_payload={"gig_job_id": str(job.id)},
        status=OrderStatus.accepted,
        requested_at=job.accepted_at or job.offered_at or now,
        pickup_address=job.pickup_address,
        pickup_window_start=job.pickup_window_open,
        pickup_window_end=job.pickup_window_close,
        delivery_address=job.dropoff_address,
        delivery_lat=job.dropoff_lat if job.dropoff_lat is not None else door_site.lat,
        delivery_lng=job.dropoff_lng if job.dropoff_lng is not None else door_site.lng,
        delivery_location_id=door_site.id,
        delivery_window_start=job.dropoff_window_open,
        delivery_window_end=job.dropoff_window_close,
    )
    session.add(order)
    await session.flush()

    counter = await handoff_point_for(session, counter_site, create=True) if counter_site is not None else None
    door = await handoff_point_for(session, door_site, create=True)
    pickup = Stop(
        id=pickup_stop_id, route_id=route.id, stop_type="pickup", sequence=1,
        status=STOP_STATUS_RECORDED, parcel_count=0,
        handoff_point_id=counter.id if counter is not None else None,
        record_source=SOURCE_OFF_PLATFORM, collected_by=str(driver_id),
    )
    dropoff = Stop(
        id=record_id, route_id=route.id, stop_type="dropoff", sequence=2,
        status=STOP_STATUS_RECORDED, parcel_count=0,
        handoff_point_id=door.id if door is not None else None,
        record_source=SOURCE_OFF_PLATFORM, collected_by=str(driver_id),
    )
    session.add_all([pickup, dropoff])
    await session.flush()
    session.add_all([StopOrder(stop_id=pickup.id, order_id=order.id), StopOrder(stop_id=dropoff.id, order_id=order.id)])
    job.record_order_id = order.id
    await session.flush()
    return OffPlatformRecord(job=job, order=order, pickup=pickup, dropoff=dropoff)


def _when(end: OffPlatformEnd) -> datetime | None:
    if end.at is None:
        return None
    at = end.at if end.at.tzinfo is not None else end.at.replace(tzinfo=timezone.utc)
    if at > datetime.now(timezone.utc) + _MAX_DEVICE_CLOCK_LEAD:
        raise RecordInvalid("that time is in the future; check the phone's clock")
    return at


def _pin(stop: Stop, end: OffPlatformEnd) -> None:
    """The end's pins onto its stop, where the handoff capture reads them: the
    stop pin as the arrival's, the counter or door as the completion's. A later
    send overwrites - a retaken pin is the better one."""
    if end.stop_pin is not None:
        stop.arrived_lat, stop.arrived_lng = end.stop_pin.lat, end.stop_pin.lng
        stop.arrived_accuracy_m = end.stop_pin.accuracy_m
    if end.door_pin is not None:
        stop.completed_lat, stop.completed_lng = end.door_pin.lat, end.door_pin.lng
        stop.completed_accuracy_m = end.door_pin.accuracy_m


async def log_off_platform_record(
    session: AsyncSession,
    *,
    driver_id: uuid.UUID,
    home: Hub,
    record_id: uuid.UUID,
    body: OffPlatformRecordBody,
) -> OffPlatformRecord:
    """Make the job's record, or add to it. Idempotent by overwrite: the outbox
    retries, and each send writes only what it carries. Flushes; the caller
    commits."""
    job = await _the_job(session, body, driver_id=driver_id, home=home)
    record = await _existing(session, job, record_id)
    if record is None:
        record = await _create(
            session, job, record_id=record_id, pickup_stop_id=body.pickup_stop_id, driver_id=driver_id, home=home
        )
    elif record.pickup.id != body.pickup_stop_id:
        raise RecordConflict("this record's pickup has another id")

    order = record.order
    if body.item is not None:
        given = body.item.model_dump(exclude_unset=True)
        if "item_description" in given:
            order.item_description = given["item_description"]
        if "item_weight_kg" in given:
            order.item_weight_kg = given["item_weight_kg"]
        if "item_size_class" in given:
            order.item_size_class = given["item_size_class"]
        if "item_hazmat_or_liquid" in given:
            order.item_hazmat_or_liquid = given["item_hazmat_or_liquid"]

    # Each end's time is the first one sent: the tap at the counter or the door.
    # A later send from either end - a correction to the item made at the door -
    # does not move it, and an end can never be later than the drop-off.
    if body.pickup is not None:
        _pin(record.pickup, body.pickup)
        picked_up_at = _when(body.pickup)
        if picked_up_at is not None and record.pickup.completed_at is None:
            if record.dropoff.completed_at is not None and picked_up_at > record.dropoff.completed_at:
                raise RecordInvalid("the pickup cannot be after the delivery")
            record.pickup.completed_at = picked_up_at
            gig_store.advance_to(job, "picked_up", at=picked_up_at)
            if order.status == OrderStatus.accepted:
                order.status = OrderStatus.picked_up

    if body.dropoff is not None:
        _pin(record.dropoff, body.dropoff)
        door = body.dropoff.door_pin
        if door is not None and (order.delivery_lat is None or order.delivery_lng is None):
            # Located by the door itself when the job's address had no coordinates.
            order.delivery_lat, order.delivery_lng = door.lat, door.lng
        delivered_at = _when(body.dropoff)
        if delivered_at is not None and record.dropoff.completed_at is None:
            if record.pickup.completed_at is not None and delivered_at < record.pickup.completed_at:
                raise RecordInvalid("the delivery cannot be before the pickup")
            record.dropoff.completed_at = delivered_at
            order.delivered_at = delivered_at
            order.status = OrderStatus.delivered
            gig_store.advance_to(job, "delivered", at=delivered_at)
            # Owed for a delivery, not for a record started.
            if job.record_bonus_cents is None:
                job.record_bonus_cents = PLACEHOLDER_RECORD_BONUS_CENTS

    await session.flush()
    return record


async def find_off_platform_record(
    session: AsyncSession, *, driver_id: uuid.UUID, record_id: uuid.UUID
) -> OffPlatformRecord:
    """This driver's record by its id - the drop-off stop's. Someone else's reads
    as none, as the gig-job endpoints do."""
    dropoff = await session.get(Stop, record_id)
    route = await session.get(Route, dropoff.route_id) if dropoff is not None else None
    if (
        dropoff is None
        or route is None
        or route.driver_id != driver_id
        or dropoff.record_source != SOURCE_OFF_PLATFORM
        or dropoff.stop_type != "dropoff"
    ):
        raise RecordNotFound("record not found")
    order_id = (
        await session.execute(select(StopOrder.order_id).where(StopOrder.stop_id == dropoff.id).limit(1))
    ).scalar_one()
    job = (await session.scalars(select(GigJob).where(GigJob.record_order_id == order_id))).one()
    record = await _existing(session, job, record_id)
    assert record is not None  # the job points at this order
    return record


async def record_grade(session: AsyncSession, stop_id: uuid.UUID) -> tuple[str | None, int | None]:
    """The record's tier now, and how many elements it is missing."""
    row = (
        await session.execute(
            text("SELECT tier, elements_missing FROM record_tiers WHERE stop_id = :stop_id"),
            {"stop_id": stop_id},
        )
    ).first()
    return (row[0], row[1]) if row is not None else (None, None)
