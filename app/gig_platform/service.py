"""
The multi-platform gig job store (docs/ROADMAP.md G3).

Every intake path writes through here and nothing writes a GigJob directly.
That is the whole reason this exists before any intake path does: manual
entry works today, the share-sheet extraction (G2) and the Android
notification listener (G1) arrive later, and swapping between them should
change which function calls `record_job` - not the schema, not the dedupe
rule, not the lifecycle. Automated intake is deliberately deferred as a
30-driver problem; at three drivers manual entry costs minutes a day.

Kept out of the route modules so the driver-facing and ops-facing surfaces
build the same view the same way, matching app/returns/service.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gig_job import GigJob
from app.schemas.gig import GigJobIntake, GigJobView

# Which transitions are real. A job can be declined or cancelled from any
# non-terminal state, but forward progress is strictly ordered - you cannot
# deliver something you never picked up, and a `delivered` job is done.
#
# Modelled as an explicit map rather than inferred from an ordering, because
# `declined` and `cancelled` are not points on the same line: declined means
# we evaluated and passed, cancelled means the platform pulled it.
_ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "offered": ("accepted", "declined", "cancelled"),
    "accepted": ("picked_up", "cancelled"),
    "picked_up": ("delivered", "cancelled"),
    "delivered": (),
    "declined": (),
    "cancelled": (),
}

# Transitions that stamp a timestamp column, so the lifecycle writes its own
# audit trail rather than relying on updated_at (which moves on any change).
_STATUS_TIMESTAMPS = {
    "accepted": "accepted_at",
    "picked_up": "picked_up_at",
    "delivered": "delivered_at",
}


class DuplicateGigJob(Exception):
    """This platform job ref is already recorded.

    Expected rather than exceptional once intake paths overlap: a
    notification and a manual entry can both capture the same offer. The
    caller decides whether that is a 409 or a silent no-op; the store's job
    is only to refuse to write it twice.
    """

    def __init__(self, existing: GigJob) -> None:
        self.existing = existing
        super().__init__(
            f"{existing.source_platform} job {existing.platform_job_ref} is already recorded"
        )


class InvalidGigJobTransition(Exception):
    """A status change that isn't a real lifecycle step."""

    def __init__(self, current: str, requested: str) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"cannot move a {current} job to {requested}")


def gig_job_view(job: GigJob) -> GigJobView:
    return GigJobView(
        gig_job_id=str(job.id),
        hub_id=str(job.hub_id),
        driver_id=str(job.driver_id) if job.driver_id else None,
        source_platform=job.source_platform,
        intake_source=job.intake_source,
        platform_job_ref=job.platform_job_ref,
        pickup_address=job.pickup_address,
        pickup_lat=float(job.pickup_lat) if job.pickup_lat is not None else None,
        pickup_lng=float(job.pickup_lng) if job.pickup_lng is not None else None,
        dropoff_address=job.dropoff_address,
        dropoff_lat=float(job.dropoff_lat) if job.dropoff_lat is not None else None,
        dropoff_lng=float(job.dropoff_lng) if job.dropoff_lng is not None else None,
        pickup_window_open=job.pickup_window_open,
        pickup_window_close=job.pickup_window_close,
        dropoff_window_open=job.dropoff_window_open,
        dropoff_window_close=job.dropoff_window_close,
        pay_cents=job.pay_cents,
        distance_miles=job.distance_miles,
        assignment_scope=job.assignment_scope,
        status=job.status,
        offered_at=job.offered_at,
        accepted_at=job.accepted_at,
        picked_up_at=job.picked_up_at,
        delivered_at=job.delivered_at,
        is_pinned_to_driver=job.is_pinned_to_driver,
        is_sequenceable=job.is_sequenceable,
    )


async def find_by_platform_ref(
    session: AsyncSession, source_platform: str, platform_job_ref: str
) -> GigJob | None:
    result = await session.execute(
        select(GigJob).where(
            GigJob.source_platform == source_platform,
            GigJob.platform_job_ref == platform_job_ref,
        )
    )
    return result.scalar_one_or_none()


async def record_job(
    session: AsyncSession,
    intake: GigJobIntake,
    *,
    hub_id: str,
    driver_id: str | None = None,
    status: str = "offered",
) -> GigJob:
    """Record one captured offer, whatever path captured it.

    Checked for duplicates before insert rather than relying on the unique
    constraint alone, so the caller gets the existing row back and can
    decide what to do with it - a bare IntegrityError would lose that.

    `driver_id` is the driver whose platform account the offer arrived on.
    Supplied for anything captured by a driver's own device; null only for an
    offer recorded centrally with no owner yet.
    """
    existing = await find_by_platform_ref(
        session, intake.source_platform, intake.platform_job_ref
    )
    if existing is not None:
        raise DuplicateGigJob(existing)

    job = GigJob(
        hub_id=uuid.UUID(hub_id),
        driver_id=uuid.UUID(driver_id) if driver_id else None,
        source_platform=intake.source_platform,
        intake_source=intake.intake_source,
        platform_job_ref=intake.platform_job_ref,
        pickup_address=intake.pickup_address,
        pickup_lat=intake.pickup_lat,
        pickup_lng=intake.pickup_lng,
        dropoff_address=intake.dropoff_address,
        dropoff_lat=intake.dropoff_lat,
        dropoff_lng=intake.dropoff_lng,
        pickup_window_open=intake.pickup_window_open,
        pickup_window_close=intake.pickup_window_close,
        dropoff_window_open=intake.dropoff_window_open,
        dropoff_window_close=intake.dropoff_window_close,
        pay_cents=intake.pay_cents,
        distance_miles=intake.distance_miles,
        assignment_scope=intake.assignment_scope,
        status=status,
        offered_at=intake.offered_at,
        accepted_at=datetime.now(timezone.utc) if status == "accepted" else None,
        raw_payload=intake.raw_payload,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def transition(session: AsyncSession, job: GigJob, new_status: str) -> GigJob:
    """Move a job along its lifecycle, refusing steps that aren't real.

    Idempotent on a repeated identical status: a driver double-tapping
    "picked up" on a flaky connection should not be an error, and the
    timestamp of the first one is the true one.
    """
    if job.status == new_status:
        return job

    if new_status not in _ALLOWED_TRANSITIONS[job.status]:
        raise InvalidGigJobTransition(job.status, new_status)

    job.status = new_status
    stamp = _STATUS_TIMESTAMPS.get(new_status)
    if stamp is not None and getattr(job, stamp) is None:
        setattr(job, stamp, datetime.now(timezone.utc))

    await session.commit()
    await session.refresh(job)
    return job


async def list_for_driver(session: AsyncSession, driver_id: str) -> list[GigJob]:
    """A driver's jobs across every platform, in the order they'd work them.

    The point of the store being multi-platform: one ordered day spanning
    Curri, Dispatch and Roadie rather than three separate apps to reconcile
    by hand (G6 builds the driver-facing surface on top of this).
    """
    result = await session.execute(
        select(GigJob)
        .where(GigJob.driver_id == uuid.UUID(driver_id))
        .order_by(GigJob.pickup_window_open)
    )
    return list(result.scalars().all())


async def list_for_hub(
    session: AsyncSession, hub_id: str, *, statuses: tuple[str, ...] | None = None
) -> list[GigJob]:
    """A hub's jobs, newest offer first.

    Backs the density and volume instrumentation (G12) that decides when
    batching becomes possible at all - offers per day, jobs per driver per
    day - so that question gets answered with data rather than argued.
    """
    query = select(GigJob).where(GigJob.hub_id == uuid.UUID(hub_id))
    if statuses:
        query = query.where(GigJob.status.in_(statuses))
    result = await session.execute(query.order_by(GigJob.offered_at.desc().nullslast()))
    return list(result.scalars().all())
