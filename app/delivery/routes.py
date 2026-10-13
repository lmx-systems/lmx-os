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

    **Taken before any of the route's stops** (docs/ARCHITECTURE.md, lock
    order). Every path that changes a live route - a driver arriving at,
    completing or flagging a stop, dispatch cancelling an order on it, the
    in-flight insertion, the route's current stop moving on - writes the ETAs of
    the route's other stops. Two of those each holding a stop and then reaching
    for the other's deadlocked; holding the route first makes the route the one
    thing they queue on.

    It is also what closes a route exactly once. Every path that can finish a
    route counts the stops still live and closes the route when there are none.
    Under READ COMMITTED two of those running at once each see the other's stop
    as live, and neither closes it: a route left `active` with nothing on it,
    and a driver never freed. Taking this lock before the count makes the second
    wait for the first and then count what it committed.

    Taking it again in the same transaction is free, so helpers that write a
    route's stops take it themselves rather than trusting the caller.
    """
    await session.execute(select(Route.id).where(Route.id == route_id).with_for_update())


async def try_lock_route(session: AsyncSession, route_id: uuid.UUID) -> bool:
    """Take the route's lock without waiting, or give up.

    For a caller already holding an order row: the route comes before orders,
    so one that waited here could deadlock with a driver's complete holding the
    route and waiting for that order. Returns False, with the transaction rolled
    back, when the route is busy.
    """
    try:
        await session.execute(
            select(Route.id).where(Route.id == route_id).with_for_update(nowait=True)
        )
    except DBAPIError:
        await session.rollback()
        return False
    return True


async def try_lock_route_and_stops(session: AsyncSession, route_id: uuid.UUID) -> bool:
    """Lock the route and every stop on it without waiting, or give up.

    For the refresh a location ping triggers, which is optional - the next ping, or
    the driver's next tap, refreshes the same ETAs. It takes the locks in the same
    order as everything else (the route, then its stops), but never waits: a ping
    is not worth queueing behind a driver's tap. Taking the stops as well means
    the walk reads statuses nobody can change under it, so it never writes a
    forecast onto a stop the driver has just arrived at.

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
