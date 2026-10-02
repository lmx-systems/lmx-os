"""
Hub operating calendar (docs/ROADMAP.md R6) - the one place that answers
"is this hub closed?" A closure (app/models/hub_closure.py) is a local
calendar day in the hub's own timezone, so turning a UTC instant into a
closed/open answer has to go through Hub.timezone, never raw UTC.

Consumed by the dispatch optimizer (skip a cycle for a closed hub) and the
Learning Loop's nightly scheduler (skip the nightly job on a closed day).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hub import Hub
from app.models.hub_closure import HubClosure


def hub_zone(hub: Hub | None) -> tzinfo:
    """The clock a hub keeps, or UTC's for a hub that cannot be found."""
    return ZoneInfo(hub.timezone) if hub is not None else timezone.utc


def hub_local_date(hub: Hub, at: datetime) -> date:
    """The calendar date at instant `at` in the hub's own timezone. A hub at
    11pm Pacific and one at 2am Eastern for the same UTC instant are on
    different calendar days, and a closure is a local day - so 'closed
    today' is the hub's wall clock, not UTC's."""
    return at.astimezone(ZoneInfo(hub.timezone)).date()


def hub_day_bounds(hub: Hub, day: date) -> tuple[datetime, datetime]:
    """The instants a local calendar day begins and ends at, in UTC."""
    tz = ZoneInfo(hub.timezone)
    return midnight(day, tz), midnight(day + timedelta(days=1), tz)


def hub_days(hub: Hub, since: datetime, until: datetime) -> list[tuple[datetime, datetime]]:
    """The hub's local calendar days that overlap [since, until), each as the
    two midnights that bound it.

    The wall clock's day, not UTC's. UTC midnight is 4 or 5pm in Los Angeles,
    so a day cut in UTC splits every evening in two. Across a clock change a
    day is 23 or 25 hours long, which is how long it is on the wall the wage is
    paid by.
    """
    tz = ZoneInfo(hub.timezone)
    day = since.astimezone(tz).date()
    days = []
    while (start := midnight(day, tz)) < until:
        days.append((start, midnight(day + timedelta(days=1), tz)))
        day += timedelta(days=1)
    return days


def hub_month(hub: Hub, year: int, month: int) -> tuple[datetime, datetime]:
    """The instants a calendar month begins and ends at on the hub's own clock."""
    tz = ZoneInfo(hub.timezone)
    following = date(year + month // 12, month % 12 + 1, 1)
    return midnight(date(year, month, 1), tz), midnight(following, tz)


def midnight(day: date, tz: tzinfo) -> datetime:
    """The instant `day` begins on `tz`'s clock, expressed in UTC.

    UTC because Python subtracts two datetimes that share a tzinfo by their wall
    clocks: a 25-hour day bounded in local time measures as 24, and a driver
    on duty across it would be paid for an hour less than they worked.
    """
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)


async def is_hub_closed_on(session: AsyncSession, hub_id: str, on_date: date) -> bool:
    """Whether a specific local calendar day is marked closed for this hub."""
    result = await session.execute(
        select(HubClosure.id).where(
            HubClosure.hub_id == uuid.UUID(hub_id),
            HubClosure.closure_date == on_date,
        )
    )
    return result.first() is not None


async def is_hub_closed_at(session: AsyncSession, hub_id: str, at: datetime) -> bool:
    """Whether the hub is closed at UTC instant `at`, resolving the local
    calendar day via the hub's timezone. Fails *open* (returns False) on a
    missing hub or an unparseable timezone: a data problem should never
    silently halt dispatch - the surrounding cycle will surface it, and an
    over-dispatch on a bad-data day is safer than a silent shutdown."""
    hub = await session.get(Hub, uuid.UUID(hub_id))
    if hub is None:
        return False
    try:
        local_date = hub_local_date(hub, at)
    except Exception:
        return False
    return await is_hub_closed_on(session, hub_id, local_date)
