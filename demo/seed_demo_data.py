"""
Seeds the one Hub / Client / Shop / Driver the investor demo needs so
demo/epicor_sample_order.json can actually be ingested and assigned -
without this, the ingestion endpoint would 404 with ShopNotFoundError
and there'd be no driver for the optimizer to assign to.

Idempotent: safe to run more than once (e.g. right before every demo) -
it checks for existing rows before inserting, and re-upserts the driver's
Redis fleet state either way so the driver always comes back "available"
even if a previous demo run left it "en_route".

Usage:
    python -m demo.seed_demo_data

Requires DATABASE_URL / REDIS_URL to point at the stack you want to seed
(defaults in app/config.py match `docker compose up`'s port mappings, so
this works unmodified against a local docker-compose stack).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import settings
from app.fleet_state.manager import FleetStateManager
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.shop import Shop
from app.schemas.fleet import DriverLocation, DriverState
from demo.ids import CLIENT_ID, DRIVER_ID, HUB_ID, SHOP_EXTERNAL_REF, SHOP_ID

# Hub is downtown; the shop and driver sit a couple miles apart within it -
# close enough that the stub nearest-neighbor optimizer assigns the driver to
# the order in one cycle, which is the point of the demo.
#
# **Austin, because that is where the deliveries are.** These were Atlanta until
# somebody noticed the fleet map: `run_full_loop`'s manifest carries Austin drop
# addresses, which geocode to 30.26/-97.74, so the demo showed a driver about a
# thousand kilometres from every stop on his route. The "couple miles apart"
# above stopped being true the day that manifest was written, and the map is on
# the first screen an investor looks at.
#
# Austin is also the deliberate choice `scripts/seed_austin_world.py` explains:
# the design partner is nowhere near Texas, so a synthetic row can never be
# mistaken for a real one (`CLAUDE.md`'s naming rule).
HUB_LAT, HUB_LNG = 30.2672, -97.7431
SHOP_LAT, SHOP_LNG = 30.2729, -97.7513
DRIVER_LAT, DRIVER_LNG = 30.2785, -97.7460


async def _get_or_create(session: AsyncSession, model, id_, **fields):
    """Create the row, or bring an existing one up to date.

    **It used to return an existing row untouched**, which made "safe to re-run"
    mean *skips* rather than *converges* - so moving the demo hub from Atlanta to
    Austin changed this file and nothing in the database, and the fleet map went
    on showing a driver a thousand kilometres from his own stops. A seeder whose
    second run cannot correct its first is a seeder you have to remember to
    delete around.

    Only the fields named here are written, so anything a demo run has since put
    on the row - a driver's shift state, a shop's location link - is left alone.
    """
    existing = await session.get(model, id_)
    if existing:
        changed = [
            name
            for name, value in fields.items()
            if getattr(existing, name, None) != value
        ]
        for name in changed:
            setattr(existing, name, fields[name])
        if changed:
            await session.commit()
        return existing, False
    row = model(id=id_, **fields)
    session.add(row)
    await session.commit()
    return row, True


async def seed() -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        hub, hub_created = await _get_or_create(
            session, Hub, HUB_ID,
            name="LMX Demo Hub",
            # Austin, matching the coordinates and the manifest's addresses.
            # It was America/New_York, which the hold windows and the nightly
            # job both read - a demo hub whose clock disagrees with its own
            # geography is a quiet way to produce deadlines nobody can explain.
            timezone="America/Chicago",
            lat=HUB_LAT, lng=HUB_LNG,
        )
        client, client_created = await _get_or_create(
            session, Client, CLIENT_ID,
            hub_id=HUB_ID, name="Demo Auto Parts Distributor", pos_system="epicor",
        )
        shop, shop_created = await _get_or_create(
            session, Shop, SHOP_ID,
            client_id=CLIENT_ID, name="Demo Auto Parts - Midtown", address="123 Peachtree St, Atlanta, GA",
            lat=SHOP_LAT, lng=SHOP_LNG, external_ref=SHOP_EXTERNAL_REF,
        )
        driver, driver_created = await _get_or_create(
            session, Driver, DRIVER_ID,
            hub_id=HUB_ID, name="Demo Driver - Jordan P.", phone="+14045550100",
            vehicle_capacity_units=5,
        )

    await engine.dispose()

    # Redis fleet state - always re-upserted "available" so the demo works
    # even if a prior run left the driver mid-route.
    fleet_state = FleetStateManager()
    await fleet_state.upsert_driver_state(
        DriverState(
            driver_id=str(DRIVER_ID), hub_id=str(HUB_ID), status="available",
            capacity_units=5, load_units=0,
        )
    )
    await fleet_state.update_driver_location(
        DriverLocation(
            driver_id=str(DRIVER_ID), lat=DRIVER_LAT, lng=DRIVER_LNG,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        str(HUB_ID),
    )

    print("Demo data ready:")
    print(f"  Hub    : {HUB_ID}  ({'created' if hub_created else 'already existed'})")
    print(f"  Client : {CLIENT_ID}  ({'created' if client_created else 'already existed'})")
    print(f"  Shop   : {SHOP_ID}  external_ref={SHOP_EXTERNAL_REF}  ({'created' if shop_created else 'already existed'})")
    print(f"  Driver : {DRIVER_ID}  status=available  ({'created' if driver_created else 'already existed'})")
    print()
    print("Next: python -m demo.send_demo_order")


if __name__ == "__main__":
    asyncio.run(seed())
