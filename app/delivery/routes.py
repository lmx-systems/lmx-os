"""Small helpers shared by everything that finishes a route."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.parcel import Parcel
from app.models.route import Route
from app.models.stop import Stop, StopOrder


async def lock_route(session: AsyncSession, route_id: uuid.UUID) -> Route | None:
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

    Returns the route **read fresh** under the lock (None if there is no such
    route). A caller that read the route before waiting here holds a stale
    copy: its status may have gone `completed` and its `plan_version` moved on
    while it waited. The in-flight insertion appended stops to a route a
    driver had just finished, and wrote back a `plan_version` another writer
    had already used, from exactly that (`CON-5`). Pending changes on the
    caller's copy are flushed first (autoflush), so nothing written is lost.
    """
    return (
        await session.execute(
            select(Route)
            .where(Route.id == route_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


# A stop's finished states. `cancelled` is a stop taken off its route
# (app/orders/cancellation.py); `recorded` an off-platform record's (`DE-7`).
FINISHED_STOP_STATUSES = ("completed", "failed", "cancelled", "recorded")


async def take_orders_off_stops(
    session: AsyncSession, stops: list[Stop], order_ids: list[uuid.UUID]
) -> list[str]:
    """Take these orders off these stops, on a route the caller has locked and
    with the stops locked too. Returns the ids of the stops cancelled outright.

    A stop that still carries another order stays on the route; only these
    orders' links to it go. A stop that carried nothing else is marked
    `cancelled` rather than deleted: the messages sent about it, and any flag or
    geofence crossing, refer to the row, and the route view leaves cancelled
    stops out. Its `eta` goes - there is no arrival to forecast - and its
    `planned_eta` stays, as the record of what was planned.

    A shared pickup also sheds the leaving orders' parcels (`_shed_parcels`):
    left at its old count, it could never be completed, since a scan counts only
    the parcels of orders still on the stop and completion waits for the count.

    The caller bumps the route's `plan_version`, refreshes its ETAs, promotes
    its next stop and tells the driver's app, after the commit; the app reloads
    the route on that change and sees the new count.
    """
    leaving = set(order_ids)
    removed: list[str] = []
    for stop in stops:
        carried = set(
            (await session.scalars(select(StopOrder.order_id).where(StopOrder.stop_id == stop.id))).all()
        )
        if carried - leaving:
            # Other orders still visit this stop; these just aren't among them.
            links = (
                await session.scalars(
                    select(StopOrder).where(
                        StopOrder.stop_id == stop.id, StopOrder.order_id.in_(carried & leaving)
                    )
                )
            ).all()
            for link in links:
                await session.delete(link)
            if stop.stop_type == "pickup":
                await _shed_parcels(session, stop, list(carried & leaving), list(carried - leaving))
            continue
        stop.status = "cancelled"
        stop.eta = None
        removed.append(str(stop.id))
    await session.flush()
    return removed


async def _parcels(session: AsyncSession, order_ids: list[uuid.UUID]) -> tuple[int, int]:
    """(parcels, parcels scanned) across these orders' Parcel rows."""
    total, scanned = (
        await session.execute(
            select(func.count(), func.count(Parcel.scanned_at)).where(Parcel.order_id.in_(order_ids))
        )
    ).one()
    return int(total), int(scanned)


async def _shed_parcels(
    session: AsyncSession, stop: Stop, leaving: list[uuid.UUID], staying: list[uuid.UUID]
) -> None:
    """Take the leaving orders' parcels off a pickup that keeps other orders.

    A pickup's count was built as its orders' Parcel rows, or one per order where
    an order has none (`_parcel_count_for_orders` and its callers), so the leaving
    orders take the same away: their rows, or one each. It never falls below one
    per order still on the stop. Scans of the leaving orders' parcels come off the
    scanned count too - left in, they would let the stop complete with a staying
    order's parcel unscanned. A parcel confirmed by hand is not stamped, so a hand
    count can't be told apart by order; it stays, capped at the new count.
    """
    leaving_total, leaving_scanned = await _parcels(session, leaving)
    stop.parcel_count = max(len(staying), stop.parcel_count - (leaving_total or len(leaving)))
    stop.scanned_count = min(stop.parcel_count, max(0, stop.scanned_count - leaving_scanned))


# Postgres's lock_not_available: what NOWAIT raises on a row somebody holds.
_LOCK_NOT_AVAILABLE = "55P03"


def _is_lock_busy(exc: DBAPIError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE


async def try_lock_route(session: AsyncSession, route_id: uuid.UUID) -> bool:
    """Take the route's lock without waiting, or give up.

    For a caller already holding an order row: the route comes before orders,
    so one that waited here could deadlock with a driver's complete holding the
    route and waiting for that order. Returns False, with the transaction rolled
    back, when the route is busy. Any other database error is raised: a dropped
    connection or a statement timeout is not "somebody is on this route".
    """
    try:
        await session.execute(
            select(Route.id).where(Route.id == route_id).with_for_update(nowait=True)
        )
    except DBAPIError as exc:
        if not _is_lock_busy(exc):
            raise
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

    Returns False, with the transaction rolled back, when anything is busy; any
    other database error is raised.
    """
    try:
        await session.execute(
            select(Route.id).where(Route.id == route_id).with_for_update(nowait=True)
        )
        await session.execute(
            select(Stop.id).where(Stop.route_id == route_id).with_for_update(nowait=True)
        )
    except DBAPIError as exc:
        if not _is_lock_busy(exc):
            raise
        await session.rollback()
        return False
    return True
