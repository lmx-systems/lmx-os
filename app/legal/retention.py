"""Deleting what we said we would delete.

The privacy policy in `content/privacy.md` states that a driver's location history
is kept for ninety days. Before this module nothing deleted it, which would have made
the policy the same class of defect as a proof-of-delivery requirement nobody checks
or a geocoder that caches its own failures: a promise about a thing that never
happens.

**Scope is deliberately one table.** `driver_location_pings` is the only personal
record here that grows without bound and has no other reason to exist past its
usefulness - at a 30-second ping interval an on-duty driver writes about 120 rows an
hour, so the trail accumulates forever and describes a person's movements. Everything
else the policy commits to is either already enforced elsewhere (tracking links
expire via `settings.tracking_link_grace_hours`) or is a business record with a
multi-year retention that nothing should be quietly deleting yet - proof-of-delivery
images and driver documents live in object storage, where the right mechanism is a
bucket lifecycle rule rather than an application loop, and that is called out in
`docs/LEGAL_BRIEF.md` as outstanding.

Pruning is a scheduled sweep, like the webhook one: safe to over-call, and a run with
nothing to do is one indexed delete.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.driver_location_ping import DriverLocationPing

logger = structlog.get_logger(__name__)


def location_ping_cutoff(now: datetime | None = None) -> datetime:
    """The oldest ping we are allowed to keep.

    UTC throughout. `recorded_at` is stored with a timezone, and building a cutoff
    from a local date against a UTC column is a bug that only shows up in the
    evening - it has already happened once in this codebase, in the billing tests.
    """
    reference = now or datetime.now(timezone.utc)
    return reference - timedelta(days=settings.location_ping_retention_days)


async def prune_location_pings(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """Delete location pings older than the retention period.

    Returns what it did rather than nothing, because "the sweep ran" and "the sweep
    deleted the rows it should have" are different claims and only the second one is
    worth anything. The count is read before the delete in the same transaction.
    """
    cutoff = location_ping_cutoff(now)

    doomed = (
        await session.execute(
            select(func.count())
            .select_from(DriverLocationPing)
            .where(DriverLocationPing.recorded_at < cutoff)
        )
    ).scalar_one()

    if doomed:
        await session.execute(
            delete(DriverLocationPing).where(DriverLocationPing.recorded_at < cutoff)
        )
        await session.commit()

    result = {
        "deleted": int(doomed),
        "retention_days": settings.location_ping_retention_days,
        "cutoff": cutoff.isoformat(),
    }
    logger.info("location_ping_prune_complete", **result)
    return result
