"""Write a generated Austin day into the database.

Kept apart from `austin_world.py` on purpose: the generator is pure and can be
reasoned about without a database, and the tests that grade the measurement
against planted truth need both halves but for different reasons. Splitting
them also means a test can generate a world, assert something about it, and
never touch Postgres.

Everything written here is fictional. See the generator's docstring for why the
geography is Austin and not where the design partner is.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity import resolve_location
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop
from app.models.stop_geofence_event import KIND_ENTER, KIND_EXIT, StopGeofenceEvent
from demo.austin_world import AustinWorld

# Marks every row this writes, so a dev database can be cleaned out without
# taking real seeded data with it.
SYNTHETIC_CLIENT_NAME = "Synthetic Austin (test data)"
SYNTHETIC_HUB_NAME = "Synthetic Austin hub"


@dataclass
class PersistedWorld:
    hub_id: object
    client_id: object
    shop_ids: list[object]
    route_ids: list[object]
    stop_ids: list[object]
    crossings: int


async def persist_world(
    session: AsyncSession, world: AustinWorld, *, seed: int = 1
) -> PersistedWorld:
    """Create the hub, client, shops, drivers, routes, stops and crossings.

    Stops are written `completed` with both tap columns set, because that is
    what a finished day looks like and it is the only state the dwell and
    calibration queries consider. Crossings are written where the generator
    said the fence fired - the rest exercise the tap fallback, which is a real
    path and not an edge case.
    """
    rng = random.Random(seed)

    hub = Hub(name=SYNTHETIC_HUB_NAME, lat=30.2672, lng=-97.7431)
    session.add(hub)
    await session.flush()

    client = Client(hub_id=hub.id, name=SYNTHETIC_CLIENT_NAME, pos_system="flat_file")
    session.add(client)
    await session.flush()

    shop_ids = []
    for shop in world.shops:
        location = await resolve_location(
            session, address=shop.address, lat=shop.lat, lng=shop.lng
        )
        row = Shop(
            client_id=client.id,
            name=shop.name,
            address=shop.address,
            lat=shop.lat,
            lng=shop.lng,
            external_ref=shop.account_ref,
            location_id=location.id,
        )
        session.add(row)
        await session.flush()
        shop_ids.append(row.id)

    route_ids, stop_ids, crossings = [], [], 0
    for route in world.routes:
        driver = Driver(
            hub_id=hub.id,
            name=f"Synthetic driver {route.driver_index + 1}",
            phone=f"+1512555{rng.randint(0, 9999):04d}",
            vehicle_capacity_units=40,
        )
        session.add(driver)
        await session.flush()

        row = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
        session.add(row)
        await session.flush()
        route_ids.append(row.id)

        for stop in route.stops:
            stop_row = Stop(
                route_id=row.id,
                shop_id=shop_ids[stop.shop_index],
                sequence=stop.sequence,
                status="completed",
                arrived_at=stop.tapped_arrived_at,
                completed_at=stop.tapped_completed_at,
            )
            session.add(stop_row)
            await session.flush()
            stop_ids.append(stop_row.id)

            if stop.crossing_enter_at is not None:
                session.add_all(
                    [
                        StopGeofenceEvent(
                            stop_id=stop_row.id, kind=KIND_ENTER,
                            occurred_at=stop.crossing_enter_at,
                            recorded_at=stop.crossing_enter_at,
                        ),
                        StopGeofenceEvent(
                            stop_id=stop_row.id, kind=KIND_EXIT,
                            occurred_at=stop.crossing_exit_at,
                            recorded_at=stop.crossing_exit_at,
                        ),
                    ]
                )
                crossings += 2
        await session.flush()

    return PersistedWorld(
        hub_id=hub.id,
        client_id=client.id,
        shop_ids=shop_ids,
        route_ids=route_ids,
        stop_ids=stop_ids,
        crossings=crossings,
    )
