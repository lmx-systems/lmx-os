"""Small helpers shared by everything that finishes a route."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.route import Route
from app.models.stop import Stop


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


async def try_lock_route_and_stops(session: AsyncSession, route_id: uuid.UUID) -> bool:
    """Lock the route and every stop on it without waiting, or give up.

    For the refresh a location ping triggers, which is optional - the next ping, or
    the driver's next tap, refreshes the same ETAs. The paths that change a route
    take its locks in different orders: `complete_stop` and `flag_stop_issue` lock
    a stop and then wait for the route; a dispatch cancel writes stops and then
    waits for the route; the optimizer's in-flight insertion writes the route and
    then the stops; `arrive_at_stop` and `accept_offer` lock stops only. A ping
    refresh that waited on any of them could join a deadlock with another; one that
    never waits cannot. Taking the stops as well means the walk reads statuses
    nobody can change under it, so it never writes a forecast onto a stop the
    driver has just arrived at.

    Returns False, with the transaction rolled back, when anything is busy.
    """
    try:
        await session.execute(
            select(Route.id).where(Route.id == route_id).with_for_update(nowait=True)
        )
        await session.execute(
            select(Stop.id).where(Stop.route_id == route_id).with_for_update(nowait=True)
        )
    except DBAPIError:
        await session.rollback()
        return False
    return True
