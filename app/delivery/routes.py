"""Small helpers shared by everything that finishes a route."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.route import Route


async def lock_route(session: AsyncSession, route_id: uuid.UUID) -> None:
    """Take the route row's lock for the rest of the transaction.

    Every path that can finish a route - a driver completing or flagging its last
    stop, dispatch cancelling its last order - counts the stops still live and
    closes the route when there are none. Under READ COMMITTED two of those
    running at once each see the other's stop as live, and neither closes it: a
    route left `active` with nothing on it, and a driver never freed. Taking this
    lock before the count makes the second wait for the first and then count what
    it committed.
    """
    await session.execute(select(Route.id).where(Route.id == route_id).with_for_update())
