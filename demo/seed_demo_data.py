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

from sqlalchemy import select
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
# close enough that the stub nearest-neighbor optimizer assigns the driver
# to the order in one cycle, which is the point of the demo.
HUB_LAT, HUB_LNG = 33.7490, -84.3880
SHOP_LAT, SHOP_LNG = 33.7756, -84.3963
DRIVER_LAT, DRIVER_LNG = 33.7803, -84.3900


async def _get_or_create(session: AsyncSession, model, id_, **fields):
    existing = await session.get(model, id_)
    if existing:
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
            name="LMX Demo Hub", timezone="America/New_York", lat=HUB_LAT, lng=HUB_LNG,
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
