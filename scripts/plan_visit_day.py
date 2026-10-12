#!/usr/bin/env python3
"""Plan a technician's re-drive day: one route of visits (`DE-6`).

    python scripts/plan_visit_day.py --driver <id> --region NJ --next 25            # dry run
    python scripts/plan_visit_day.py --driver <id> --region NJ --next 25 --commit
    python scripts/plan_visit_day.py --driver <id> --region NJ --sites ids.txt --commit

`--next N` takes the N most delivered-to sites in the region that no visit has
been planned for; `--sites` takes a file of location ids, one per line, visited
in that order. Either way the driver's app shows the day as its route, and each
stop runs the golden capture at the door (`app/record/visits.py`).

Like the import, it **prints counts, never sites**: the addresses are customers'
customers. A dry run is the default and rolls back. On `--commit` the driver is
put on the route in dispatch's fleet state, as accepting an offer does, so no
cycle offers them deliveries while they are out; the periodic fleet-state
rebuild would do the same within minutes.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.fleet_state.manager import FleetStateManager  # noqa: E402
from app.models.driver import Driver  # noqa: E402
from app.record.visits import VisitDayRefused, plan_visit_day, sites_to_visit  # noqa: E402
from app.schemas.fleet import DriverState  # noqa: E402


def _read_sites(path: Path) -> list[uuid.UUID]:
    return [uuid.UUID(line.strip()) for line in path.read_text().splitlines() if line.strip()]


async def _put_on_route(driver: Driver, route_id: uuid.UUID) -> None:
    manager = FleetStateManager()
    existing = await manager.get_driver_state(str(driver.hub_id), str(driver.id))
    await manager.upsert_driver_state(
        DriverState(
            driver_id=str(driver.id),
            hub_id=str(driver.hub_id),
            status="en_route",
            capacity_units=existing.capacity_units if existing else driver.vehicle_capacity_units,
            load_units=existing.load_units if existing else 0,
            current_route_id=str(route_id),
        )
    )


async def _run(driver_id: uuid.UUID, region: str, sites: list[uuid.UUID] | None, next_n: int | None, commit: bool) -> int:
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            chosen = sites if sites is not None else await sites_to_visit(session, region, next_n or 0)
            try:
                day = await plan_visit_day(session, driver_id=driver_id, region=region, location_ids=chosen)
            except VisitDayRefused as refused:
                await session.rollback()
                print(f"refused: {refused}")
                return 1
            print(f"visits planned: {len(day.stop_ids)} in {region}, route {day.route_id}")
            if not commit:
                await session.rollback()
                print("dry run: rolled back; --commit to keep")
                return 0
            driver = await session.get_one(Driver, driver_id)
            await session.commit()
            await _put_on_route(driver, day.route_id)
            print("committed; the driver is on the route")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--driver", required=True, type=uuid.UUID, help="the technician's driver id")
    parser.add_argument("--region", required=True, help="the sites' region, e.g. NJ")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--next", type=int, help="the N most delivered-to sites not yet planned for")
    which.add_argument("--sites", type=Path, help="a file of location ids, one per line, in visiting order")
    parser.add_argument("--commit", action="store_true", help="keep the day")
    args = parser.parse_args()
    sites = _read_sites(args.sites) if args.sites else None
    return asyncio.run(_run(args.driver, args.region, sites, args.next, args.commit))


if __name__ == "__main__":
    raise SystemExit(main())
