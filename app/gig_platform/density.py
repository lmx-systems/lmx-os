"""
Density & volume instrumentation for the gig-platform path
(docs/ROADMAP.md G12).

This exists to settle one argument with data: **when does batching become
possible at all?** The roadmap is blunt that three drivers will not produce
batchable density - the pilot ran ~1.8 jobs/driver/day, and three drivers
across three platforms in a metro the size of Austin is perhaps 6-12
offers/day, where two jobs rarely overlap in both time and space. Pairing
opportunities plausibly need 10-15 drivers.

So the near-term value of the gig path is accept/decline discipline and data
accumulation, NOT batching - and this module is what stops that distinction
from eroding. If the sequenced share stays near zero as drivers are added,
that is the answer, and it should be visible rather than argued about.

It is also the trigger for revisiting automated intake (G1/G2), which was
deliberately deferred as a 30-driver problem: offers/day is the number that
says when manual entry stops being minutes a day.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gig_job import GigJob
from app.schemas.gig import GigDensityReport

# Rich's two-week Austin pilot, carried here so every report renders against
# the control group rather than against nothing. One driver, 23 commercial
# jobs. Any claim that LMX OS improved things is measured against this.
PILOT_JOBS_PER_DRIVER_PER_DAY = 1.8

# Statuses that represent a job someone actually committed to, as opposed to
# an offer that was merely seen. Declined offers count toward supply
# (offers/day) but never toward throughput.
_COMMITTED = ("accepted", "picked_up", "delivered")


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _sequenced_job_ids(jobs: list[GigJob]) -> set[uuid.UUID]:
    """Which delivered jobs were held concurrently with another job.

    "Part of a multi-job sequence" is defined here as *overlapping
    possession*: the driver had accepted this job and not yet delivered it
    while the same was true of another. That is the honest operational
    reading - two jobs in the vehicle's plan at once - and it is the only
    definition computable from what the platforms actually tell us.

    Note what it deliberately does NOT count: two jobs done back-to-back
    with no overlap. Those are sequential work, not a pairing, and counting
    them would inflate exactly the number this module exists to keep honest.
    """
    by_driver: dict[uuid.UUID, list[GigJob]] = defaultdict(list)
    for job in jobs:
        if job.driver_id and job.accepted_at and job.delivered_at:
            by_driver[job.driver_id].append(job)

    sequenced: set[uuid.UUID] = set()
    for driver_jobs in by_driver.values():
        for i, job in enumerate(driver_jobs):
            for other in driver_jobs[i + 1 :]:
                if _overlaps(
                    job.accepted_at, job.delivered_at, other.accepted_at, other.delivered_at
                ):
                    sequenced.add(job.id)
                    sequenced.add(other.id)
    return sequenced


async def hub_density_report(
    session: AsyncSession, hub_id: str, *, days: int = 14
) -> GigDensityReport:
    """Volume and pairing figures for one hub over a trailing window.

    Windowed on `offered_at` where present and `created_at` otherwise, since
    a manually-entered job may genuinely not know when the platform surfaced
    it (an automated intake path always will).
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await session.execute(
        select(GigJob).where(GigJob.hub_id == uuid.UUID(hub_id))
    )
    all_jobs = list(result.scalars().all())
    jobs = [j for j in all_jobs if (j.offered_at or j.created_at) >= since]

    committed = [j for j in jobs if j.status in _COMMITTED]
    delivered = [j for j in jobs if j.status == "delivered"]
    declined = [j for j in jobs if j.status == "declined"]

    # Distinct drivers who actually worked, not everyone on the roster - a
    # driver who took nothing this window would drag jobs/driver/day toward
    # zero and make the fleet look less productive than it was.
    active_driver_ids = {j.driver_id for j in committed if j.driver_id}

    # Days a driver was actually working, summed across drivers. Using
    # calendar days in the window instead would understate throughput for
    # part-time drivers, which is most of them.
    driver_days: set[tuple[uuid.UUID, str]] = set()
    for job in committed:
        if job.driver_id:
            stamp = job.accepted_at or job.offered_at or job.created_at
            driver_days.add((job.driver_id, stamp.date().isoformat()))

    sequenced = _sequenced_job_ids(delivered)
    # Denominator is only jobs we can actually judge - a delivered job
    # missing accepted_at can't be tested for overlap, and silently counting
    # it as un-sequenced would bias the number downward.
    measurable = [j for j in delivered if j.accepted_at and j.delivered_at]

    return GigDensityReport(
        hub_id=hub_id,
        window_days=days,
        total_offers=len(jobs),
        offers_per_day=round(len(jobs) / days, 2) if days else 0.0,
        accepted_count=len(committed),
        declined_count=len(declined),
        delivered_count=len(delivered),
        acceptance_rate=round(len(committed) / len(jobs), 3) if jobs else None,
        active_driver_count=len(active_driver_ids),
        jobs_per_driver_per_day=(
            round(len(committed) / len(driver_days), 2) if driver_days else None
        ),
        pilot_jobs_per_driver_per_day=PILOT_JOBS_PER_DRIVER_PER_DAY,
        measurable_delivered_count=len(measurable),
        sequenced_delivered_count=len(sequenced),
        sequenced_share=(
            round(len(sequenced) / len(measurable), 3) if measurable else None
        ),
    )
