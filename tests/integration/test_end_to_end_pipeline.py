"""
The full pipeline against real services: ingest an order (real Postgres +
real Redis hold queue) -> register an available driver (real Redis fleet
state) -> run one Dispatch Optimizer cycle (stub route client, since no
Google Cloud credentials exist in test) -> verify a real assignment comes
out the other end.

This is the test that would have caught a real integration bug none of
the fakeredis/pure-function unit tests could - e.g. a Redis pipeline
behaving subtly differently, or a query joining the wrong FK.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.batch_queue.store import HoldQueueStore
from app.fleet_state.manager import FleetStateManager
from app.ingestion.service import ingest_order
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import OrderStatus
from app.models.shop import Shop
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation, DriverState

pytestmark = pytest.mark.integration


async def test_full_pipeline_ingest_to_optimizer_assignment(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # Committed in FK dependency order - see the comment in
    # test_ingestion_integration.py's _seed_hub_client_shop for why a
    # single add_all + commit isn't safe here (no relationship() mappings
    # to drive SQLAlchemy's flush ordering).
    db_session.add(Hub(id=hub_id, name="E2E Integration Hub", lat=34.05, lng=-118.25))
    await db_session.commit()

    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()

    db_session.add_all(
        [
            Shop(
                id=shop_id,
                client_id=client_id,
                name="Test Shop",
                address="123 Main St",
                lat=34.051,
                lng=-118.251,
                external_ref="SHOP-E2E",
            ),
            Driver(
                id=driver_id,
                hub_id=hub_id,
                name="Test Driver",
                phone="+15555550100",
                vehicle_capacity_units=5,
            ),
        ]
    )
    await db_session.commit()

    # 1. Ingest an order - real Postgres write + real Redis hold-queue write.
    hold_queue = HoldQueueStore()
    payload = {
        "order_ref": "ORD-E2E-1",
        "shop_ref": "SHOP-E2E",
        "shop_lat": 34.051,
        "shop_lng": -118.251,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    order = await ingest_order(
        db_session,
        hold_queue,
        hub_id=str(hub_id),
        client_id=str(client_id),
        source_system="flat_file",
        payload=payload,
    )
    assert order.sla_tier == "T2"

    held_before_cycle = await hold_queue.get_all(str(hub_id))
    assert len(held_before_cycle) == 1

    # 2. Register the driver as available with a location - real Redis fleet state.
    fleet_state = FleetStateManager()
    await fleet_state.upsert_driver_state(
        DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5)
    )
    await fleet_state.update_driver_location(
        DriverLocation(
            driver_id=str(driver_id),
            lat=34.0511,
            lng=-118.2511,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        str(hub_id),
    )

    # 3. Run one Dispatch Optimizer cycle. No GOOGLE_CLOUD_PROJECT_ID is set
    # in the test environment, so this exercises the real batch-hold
    # decision logic + StubRouteOptimizationClient, all against real
    # Postgres/Redis-backed state.
    service = DispatchOptimizerService()
    result = await service.run_cycle(str(hub_id))

    assert result.engine == "stub_nearest_neighbor"
    assert len(result.assignments) == 1
    assert result.assignments[0].driver_id == str(driver_id)
    assert result.assignments[0].stop_ids == [str(order.id)]
    assert result.unassigned_stop_ids == []

    # 4. The assigned order should have been removed from the hold queue.
    held_after_cycle = await hold_queue.get_all(str(hub_id))
    assert held_after_cycle == []

    # 5. run_cycle writes the dispatch back to Postgres on its own session
    # (see app/optimizer/service.py) - db_session won't see it without an
    # explicit refresh, since that write went through a different
    # connection/session entirely.
    await db_session.refresh(order)
    assert order.status == OrderStatus.assigned
    assert order.assigned_at is not None
