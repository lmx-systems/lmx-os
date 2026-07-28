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
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hub import Hub
from app.models.hub_closure import HubClosure


def hub_local_date(hub: Hub, at: datetime) -> date:
    """The calendar date at instant `at` in the hub's own timezone. A hub at
    11pm Pacific and one at 2am Eastern for the same UTC instant are on
    different calendar days, and a closure is a local day - so 'closed
    today' is the hub's wall clock, not UTC's."""
    return at.astimezone(ZoneInfo(hub.timezone)).date()


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
