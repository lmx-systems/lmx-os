"""
Load/performance test against the design doc's Section 9 target
(docs/ROADMAP.md T1): a full Dispatch Optimizer cycle must complete in
<5s for a hub with up to 20 drivers / 100 open orders. Never tested under
anything resembling that scale before this.

Tests the stub nearest-neighbor engine (app/optimizer/google_routes_client.py's
StubRouteOptimizationClient) - no live Google Route Optimization
credentials exist in this environment (docs/NEXT_STEPS.md item 4/E1), so
this measures this codebase's own overhead at the target scale (hold-cycle
evaluation, Redis reads, the Postgres writeback), not Google's API
latency, which is a separate external dependency this can't load-test
without real credentials. Real Order/Driver rows are seeded in Postgres,
not just Redis/hold-queue data, so the writeback step actually does the
same amount of real work a live cycle would (updating real rows), rather
than silently no-op'ing against ids that don't exist anywhere.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.fleet_state.manager import FleetStateManager
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation, DriverState

pytestmark = pytest.mark.integration

DESIGN_DOC_DRIVER_COUNT = 20
DESIGN_DOC_ORDER_COUNT = 100
_SLA_TIERS = ["T1", "T2", "T3"]


async def _seed_load(db_session, hub_id: uuid.UUID, *, driver_count: int, order_count: int) -> None:
    client_id, shop_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Load Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Load Test Client", pos_system="flat_file"))
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id, client_id=client_id, name="Load Test Shop", address="1 Load Test Way",
            lat=34.05, lng=-118.25, external_ref="SHOP-LOAD-TEST",
        )
    )
    await db_session.commit()

    now = datetime.now(timezone.utc)

    driver_ids = [uuid.uuid4() for _ in range(driver_count)]
    db_session.add_all(
        [
            Driver(id=driver_ids[i], hub_id=hub_id, name=f"Load Driver {i}", phone=f"+1555{i:07d}", vehicle_capacity_units=5)
            for i in range(driver_count)
        ]
    )
    await db_session.commit()

    order_ids = [uuid.uuid4() for _ in range(order_count)]
    db_session.add_all(
        [
            Order(
                id=order_ids[i], hub_id=hub_id, client_id=client_id, shop_id=shop_id,
                external_order_ref=f"ORD-LOAD-{i}", source_system="flat_file", raw_payload={},
                sla_tier=_SLA_TIERS[i % 3],
                # Already elapsed - forces immediate release regardless of
                # clustering (evaluate_held_order's "SLA deadline always
                # wins" rule), so this cycle actually has the full order
                # set to assign rather than most of it staying held.
                hold_deadline=now - timedelta(minutes=1),
                weight_units=1, status=OrderStatus.held, requested_at=now - timedelta(minutes=30),
                delivery_address=f"{i} Delivery Rd", delivery_lat=34.05 + (i % 10) * 0.01, delivery_lng=-118.25 + (i // 10) * 0.01,
            )
            for i in range(order_count)
        ]
    )
    await db_session.commit()

    fleet_state = FleetStateManager()
    for i, driver_id in enumerate(driver_ids):
        # Spread across a small area so distance math has real variance
        # to chew through rather than every candidate being equidistant.
        lat, lng = 34.05 + (i % 5) * 0.01, -118.25 + (i // 5) * 0.01
        await fleet_state.upsert_driver_state(
            DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5)
        )
        await fleet_state.update_driver_location(
            DriverLocation(driver_id=str(driver_id), lat=lat, lng=lng, recorded_at=now.isoformat()), str(hub_id)
        )

    hold_queue = HoldQueueStore()
    for i, order_id in enumerate(order_ids):
        await hold_queue.add(
            str(hub_id),
            HeldOrder(
                order_id=str(order_id),
                shop_lat=34.05 + (i % 10) * 0.008, shop_lng=-118.25 + (i // 10) * 0.008,
                sla_tier=_SLA_TIERS[i % 3],
                hold_deadline=now - timedelta(minutes=1),
                held_since=now - timedelta(minutes=30),
                shop_name="Load Test Shop",
            ),
        )


async def test_full_cycle_completes_within_design_budget_at_target_scale(db_session, real_redis_client):
    hub_id = uuid.uuid4()
    await _seed_load(db_session, hub_id, driver_count=DESIGN_DOC_DRIVER_COUNT, order_count=DESIGN_DOC_ORDER_COUNT)

    service = DispatchOptimizerService()
    wall_clock_start = time.perf_counter()
    result = await service.run_cycle(str(hub_id))
    wall_clock_elapsed = time.perf_counter() - wall_clock_start

    # Confirms this actually exercised the full seeded scale, not an
    # empty/degenerate cycle that happened to finish fast because there
    # was nothing to do. Each RouteAssignment covers multiple stops (one
    # per driver, several orders each), so this sums stop_ids across all
    # of them rather than just counting assignment groups.
    assigned_stop_count = sum(len(a.stop_ids) for a in result.assignments)
    assert result.engine == "stub_nearest_neighbor"
    assert assigned_stop_count + len(result.unassigned_stop_ids) == DESIGN_DOC_ORDER_COUNT

    assert result.over_budget is False
    assert result.duration_seconds < settings.optimizer_cycle_budget_seconds
    assert wall_clock_elapsed < settings.optimizer_cycle_budget_seconds


async def test_full_cycle_at_5x_design_scale_still_completes_comfortably(db_session, real_redis_client):
    """Not a contractual target like the test above - the design doc only
    commits to 20 drivers / 100 orders. This is a genuine stress probe:
    how much headroom actually exists past the stated minimum, and would
    a real algorithmic regression (e.g. an accidental O(n^2) blowup) show
    up before it ever reached production. Asserts a generous multiple of
    the real budget instead of the budget itself, so this doesn't start
    failing the moment the design target is deliberately exceeded."""
    hub_id = uuid.uuid4()
    driver_count, order_count = DESIGN_DOC_DRIVER_COUNT * 5, DESIGN_DOC_ORDER_COUNT * 5
    await _seed_load(db_session, hub_id, driver_count=driver_count, order_count=order_count)

    service = DispatchOptimizerService()
    wall_clock_start = time.perf_counter()
    result = await service.run_cycle(str(hub_id))
    wall_clock_elapsed = time.perf_counter() - wall_clock_start

    assigned_stop_count = sum(len(a.stop_ids) for a in result.assignments)
    assert assigned_stop_count + len(result.unassigned_stop_ids) == order_count
    assert wall_clock_elapsed < settings.optimizer_cycle_budget_seconds * 3
