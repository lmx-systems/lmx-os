"""
Load/performance test for the Dispatch Optimizer (roadmap item T1).

The technical design's Section 9 target: a full cycle must complete in
<5 seconds for a hub with up to 20 drivers / 100 open orders. This seeds
exactly that load against real Postgres + Redis and times run_cycle
end-to-end - fleet snapshot read, hold-cycle evaluation, optimization,
hold-queue removal, Postgres status write-back, offer creation.

Caveat, stated rather than hidden: with no Google Cloud project
configured (roadmap item E1), the optimization step runs the built-in
nearest-driver stub, not the live Route Optimization API. This test
therefore validates *our* pipeline overhead - Redis round-trips, the
per-driver location reads, the write-backs - which is the part we own.
The live API's network latency gets measured under E1's live
verification, against the same budget.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.fleet_state.manager import FleetStateManager
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation, DriverState

pytestmark = pytest.mark.integration

DRIVER_COUNT = 20
ORDER_COUNT = 100
BUDGET_SECONDS = 5.0


async def _seed_load(db_session, real_redis_client):
    hub_id = uuid.uuid4()
    client_id = uuid.uuid4()
    shop_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Load Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file")
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id, client_id=client_id, name="Load Test Shop",
            address="1 Load St", lat=34.06, lng=-118.24, external_ref="LOAD-1",
        )
    )
    await db_session.commit()

    manager = FleetStateManager()
    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(DRIVER_COUNT):
        driver_id = uuid.uuid4()
        db_session.add(
            Driver(
                id=driver_id, hub_id=hub_id, name=f"Load Driver {i}",
                phone=f"+1555550{i:04d}", vehicle_capacity_units=10,
            )
        )
        await manager.upsert_driver_state(
            DriverState(
                driver_id=str(driver_id), hub_id=str(hub_id), status="available",
                capacity_units=10, load_units=0,
            )
        )
        await manager.update_driver_location(
            DriverLocation(
                driver_id=str(driver_id),
                lat=34.0 + (i % 10) * 0.01,
                lng=-118.3 + (i // 10) * 0.01,
                recorded_at=now_iso,
            ),
            str(hub_id),
        )
    await db_session.commit()

    store = HoldQueueStore()
    now = datetime.now(timezone.utc)
    for i in range(ORDER_COUNT):
        order_id = uuid.uuid4()
        db_session.add(
            Order(
                id=order_id, hub_id=hub_id, client_id=client_id, shop_id=shop_id,
                external_order_ref=f"LOAD-{i}", source_system="flat_file", raw_payload={},
                sla_tier="T2", status=OrderStatus.held, requested_at=now,
            )
        )
        await store.add(
            str(hub_id),
            HeldOrder(
                order_id=str(order_id),
                shop_lat=34.0 + (i % 20) * 0.005,
                shop_lng=-118.3 + (i % 15) * 0.005,
                sla_tier="T2",
                # Past deadline -> every order releases this cycle: worst case.
                hold_deadline=now - timedelta(minutes=1),
                held_since=now - timedelta(minutes=46),
                shop_name="Load Test Shop",
            ),
        )
    await db_session.commit()
    return str(hub_id)


async def test_full_cycle_under_budget_at_design_load(db_session, real_redis_client):
    hub_id = await _seed_load(db_session, real_redis_client)

    service = DispatchOptimizerService()
    started = time.perf_counter()
    result = await service.run_cycle(hub_id)
    wall_clock = time.perf_counter() - started

    # The service's own measurement and ours should both be under budget.
    assert result.duration_seconds < BUDGET_SECONDS, (
        f"optimizer cycle took {result.duration_seconds}s at design load "
        f"({DRIVER_COUNT} drivers / {ORDER_COUNT} orders) - budget is {BUDGET_SECONDS}s"
    )
    assert wall_clock < BUDGET_SECONDS
    assert result.over_budget is False

    # Sanity: the cycle actually did the work, not an early no-op return.
    assigned = {stop_id for a in result.assignments for stop_id in a.stop_ids}
    assert len(assigned) + len(result.unassigned_stop_ids) == ORDER_COUNT
    assert len(assigned) > 0
    print(
        f"\nT1 load test: {result.duration_seconds}s for {DRIVER_COUNT} drivers / "
        f"{ORDER_COUNT} orders (budget {BUDGET_SECONDS}s), engine={result.engine}, "
        f"assigned={len(assigned)}, unassigned={len(result.unassigned_stop_ids)}"
    )
