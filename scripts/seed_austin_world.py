"""Fill a database with a synthetic Austin delivery day.

    python scripts/seed_austin_world.py --routes 40 --stops-per-route 20
    python scripts/seed_austin_world.py --dry-run          # counts only

For working at volume against a dev stack. The tests use the same generator
with a small slice, so there is one definition of the world rather than a
fixture and a seeder that drift apart.

Everything written is fictional and Austin is deliberate - the design partner
is nowhere near Texas, so a synthetic row can never be mistaken for a real one
(CLAUDE.md's naming rule). Rows are tagged with the names in
`demo/austin_persist.py`, so a dev database can be cleaned of them.

`--dry-run` is the default. Seeding hundreds of routes into whatever
DATABASE_URL happens to point at is not something to do by accident.
"""
import argparse
import asyncio
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.identity import classification_coverage, classify_unlabelled_locations  # noqa: E402
from app.reporting.geofence_calibration import measure_geofence_calibration  # noqa: E402
from demo.austin_persist import persist_world  # noqa: E402
from demo.austin_world import generate_world  # noqa: E402


async def _run(args) -> int:
    world = generate_world(
        seed=args.seed,
        shops=args.shops,
        routes=args.routes,
        stops_per_route=args.stops_per_route,
        geofence_radius_m=args.radius,
        miss_rate=args.miss_rate,
    )
    stops = world.all_stops
    print(f"generated         {len(world.shops)} shops, {len(world.routes)} routes, "
          f"{len(stops)} stops")
    print(f"planted dwell     median {statistics.median(world.true_dwells):.0f}s, "
          f"{sum(1 for d in world.true_dwells if d < 60) / len(stops):.0%} under 60s")
    print(f"geofence radius   {world.geofence_radius_m:.0f}m "
          f"-> {world.expected_lead_seconds:.1f}s lead, "
          f"{world.expected_dwell_bias_seconds:.1f}s dwell overstatement")

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            written = await persist_world(session, world, seed=args.seed)
            print(f"written           {len(written.shop_ids)} shops, "
                  f"{len(written.stop_ids)} stops, {written.crossings} crossings")

            classified = await classify_unlabelled_locations(session)
            coverage = await classification_coverage(session)
            print(f"node classes      {classified['labelled']} labelled, "
                  f"{coverage['unlabelled_pct']:.1f}% unlabelled")

            reading = await measure_geofence_calibration(session)
            print(f"calibration       coverage {reading.coverage:.0%}, "
                  f"lead p50 {reading.lead_p50_seconds:.1f}s "
                  f"p90 {reading.lead_p90_seconds:.1f}s"
                  if reading.lead_p50_seconds is not None
                  else "calibration       no comparable stops")

            if args.commit:
                await session.commit()
                print("\ncommitted to", settings.database_url.rsplit("@", 1)[-1])
            else:
                await session.rollback()
                print("\ndry run - rolled back, nothing kept")
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--shops", type=int, default=40)
    parser.add_argument("--routes", type=int, default=8)
    # Above 18 is where DRV-1's rolling window can no longer hold the whole
    # route, which is the case worth generating.
    parser.add_argument("--stops-per-route", type=int, default=20)
    parser.add_argument("--radius", type=float, default=75.0)
    parser.add_argument("--miss-rate", type=float, default=0.08)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--commit", action="store_true")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
