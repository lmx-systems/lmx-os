"""Clear the demo hub's routes and orders, leaving the seed intact.

    python -m demo.reset            # say what it would delete
    python -m demo.reset --confirm  # actually delete it

**Why this exists.** New orders join the demo driver's *existing* active route
rather than starting a new one, so runs accumulate: three rehearsals left 36
outstanding stops on the board. That reads badly on the ops console in front of
an audience, and `INVESTOR_RUNBOOK.md` told you to reset without giving you a
way to do it.

**What it keeps:** the hub, client, shop and driver `seed_demo_data` creates,
and the ops and client logins. Those are setup, not run output, and deleting
them would mean redoing the whole §2 of the runbook.

**What it deletes:** every order on the demo hub and every route and stop
belonging to the demo driver. Re-run `seed_demo_data` afterwards to put the
driver back on shift - it already resets fleet state for exactly this case.
Proof-of-delivery photos on disk are left alone - they are outside
the database, cheap, and `PHOTO_STORAGE_DIR` is a directory somebody can empty
if they care.

**Dry by default**, like `scripts/seed_austin_world.py`. Pointing a delete at
whatever `DATABASE_URL` happens to name is not something to do by accident, and
this one takes no hub argument precisely so it can only ever touch the demo
hub's own id.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.order import Order
from app.models.route import Route
from app.models.stop import Stop
from demo.ids import DRIVER_ID, HUB_ID


async def _count(session: AsyncSession) -> tuple[int, int, int]:
    orders = await session.scalar(
        select(func.count()).select_from(Order).where(Order.hub_id == HUB_ID)
    )
    routes = await session.scalar(
        select(func.count()).select_from(Route).where(Route.driver_id == DRIVER_ID)
    )
    stops = await session.scalar(
        select(func.count())
        .select_from(Stop)
        .where(Stop.route_id.in_(select(Route.id).where(Route.driver_id == DRIVER_ID)))
    )
    return int(orders or 0), int(routes or 0), int(stops or 0)


# Everything that points at a row we are about to remove has to go first, and
# there are fifteen such tables today - `messages`, `calls`, `parcels`,
# `cod_collections`, `stop_geofence_events` and the rest. Listing them here
# would be a list that rots the first time somebody adds a sixteenth, and the
# failure would be a foreign-key error in front of an audience.
#
# So the graph is read from the database at run time. A table nobody has written
# yet is handled by this the day it exists.
_CHILDREN_OF = text(
    """
    SELECT tc.table_name, kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
    JOIN information_schema.constraint_column_usage ccu
      ON tc.constraint_name = ccu.constraint_name
    WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = :parent
    """
)


async def _delete_rows(session: AsyncSession, table: str, column: str, ids_sql: str) -> None:
    """Delete this table's rows pointing at the doomed ones, children first.

    Depth-first and one level at a time. Two passes is enough for the shape
    here, and a cycle cannot arise because a row never points at its own table
    in this schema - if one ever did, the recursion guard below stops it rather
    than looping.
    """
    for child, child_column in (
        await session.execute(_CHILDREN_OF, {"parent": table})
    ).all():
        if child == table:
            continue
        await _delete_rows(
            session,
            child,
            child_column,
            f"SELECT id FROM {table} WHERE {column} IN ({ids_sql})",
        )
    await session.execute(text(f"DELETE FROM {table} WHERE {column} IN ({ids_sql})"))


async def _delete_tree(session: AsyncSession) -> None:
    routes = f"SELECT id FROM routes WHERE driver_id = '{DRIVER_ID}'"
    stops = f"SELECT id FROM stops WHERE route_id IN ({routes})"
    orders = f"SELECT id FROM orders WHERE hub_id = '{HUB_ID}'"

    # Stops before routes, and orders last: `stop_orders` bridges the two, so
    # taking orders first would strand it.
    for table, column, ids in (
        ("stops", "id", stops),
        ("routes", "id", routes),
        ("orders", "id", orders),
    ):
        await _delete_rows(session, table, column, ids)
    await session.commit()


async def _run(confirm: bool) -> int:
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as session:
            orders, routes, stops = await _count(session)
            print(
                f"demo hub {HUB_ID}\n"
                f"  orders {orders}\n"
                f"  routes {routes}\n"
                f"  stops  {stops}"
            )
            if not confirm:
                print("\nDry run. Pass --confirm to delete.")
                return 0
            if not (orders or routes or stops):
                print("\nNothing to delete.")
                return 0

            await _delete_tree(session)
            print(
                "\nDeleted. The seed (hub, client, shop, driver, logins) is untouched."
                "\n\nRun `python -m demo.seed_demo_data` before the next loop: the"
                "\noptimizer reads Redis, not Postgres, for who is available, and the"
                "\nseeder resets the driver to available with an empty load - which it"
                "\nalready does deliberately, 'even if a prior run left the driver"
                "\nmid-route'. Duplicating that here would be a second definition of"
                "\nwhat a clean driver looks like."
            )
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm", action="store_true", help="actually delete, rather than counting"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.confirm))


if __name__ == "__main__":
    raise SystemExit(main())
