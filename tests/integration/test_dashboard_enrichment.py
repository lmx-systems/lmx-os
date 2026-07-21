"""
Integration coverage for the three dashboard data gaps closed after the
orchestrator dashboard redesign (see docs/ARCHITECTURE.md / NEXT_STEPS.md
item 11): shop_name + cluster_mate_ids on held orders, driver display
name on the fleet roster, and a queryable "last cycle" snapshot.

Calls the route functions directly (bypassing HTTP) the same way
tests/test_dispatch_event_wiring.py does - these aren't testing FastAPI's
routing, just the enrichment logic each handler adds.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api.routes import get_last_cycle, list_fleet_overview, list_held_orders, list_hubs
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.fleet_state.manager import FleetStateManager
from app.models.driver import Driver
from app.models.hub import Hub
from app.optimizer.last_cycle_store import LastCycleStore
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.optimizer import LastCycleSnapshot

pytestmark = pytest.mark.integration


async def test_held_orders_include_shop_name_and_cluster_mates(real_redis_client):
    hub_id = str(uuid.uuid4())
    hold_queue = HoldQueueStore()
    now = datetime.now(timezone.utc)

    # Two orders ~0.1 miles apart (well within the 0.8mi default radius) at
    # the same shop, one far-away order that shouldn't cluster with either.
    await hold_queue.add(
        hub_id,
        HeldOrder(
            order_id="near-1", shop_lat=34.0500, shop_lng=-118.2500, sla_tier="T2",
            hold_deadline=now + timedelta(minutes=30), held_since=now, shop_name="Midtown Auto Parts",
        ),
    )
    await hold_queue.add(
        hub_id,
        HeldOrder(
            order_id="near-2", shop_lat=34.0510, shop_lng=-118.2510, sla_tier="T2",
            hold_deadline=now + timedelta(minutes=30), held_since=now, shop_name="Midtown Auto Parts",
        ),
    )
    await hold_queue.add(
        hub_id,
        HeldOrder(
            order_id="far-1", shop_lat=35.5, shop_lng=-119.5, sla_tier="T2",
            hold_deadline=now + timedelta(minutes=30), held_since=now, shop_name="Southgate Auto",
        ),
    )

    views = await list_held_orders(hub_id)
    by_id = {v.order_id: v for v in views}

    assert by_id["near-1"].shop_name == "Midtown Auto Parts"
    assert by_id["near-2"].cluster_mate_ids == ["near-1"]
    assert by_id["near-1"].cluster_mate_ids == ["near-2"]
    assert by_id["far-1"].cluster_mate_ids == []
    assert by_id["far-1"].shop_name == "Southgate Auto"


async def test_fleet_overview_populates_driver_name_from_postgres(db_session, real_redis_client):
    hub_id, driver_id, orphan_driver_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    db_session.add(Hub(id=hub_id, name="Enrichment Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(
        Driver(id=driver_id, hub_id=hub_id, name="Jordan P.", phone="+15555550100", vehicle_capacity_units=5)
    )
    await db_session.commit()

    fleet_state = FleetStateManager()
    await fleet_state.upsert_driver_state(
        DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5)
    )
    # A driver present in Redis fleet state but with no matching Postgres
    # row - shouldn't crash the enrichment, just leave name unset.
    await fleet_state.upsert_driver_state(
        DriverState(driver_id=str(orphan_driver_id), hub_id=str(hub_id), status="off_shift", capacity_units=1)
    )

    roster = await list_fleet_overview(str(hub_id), session=db_session)
    by_id = {d.driver_id: d for d in roster}

    assert by_id[str(driver_id)].name == "Jordan P."
    assert by_id[str(orphan_driver_id)].name is None


async def test_last_cycle_snapshot_roundtrip(real_redis_client):
    hub_id = str(uuid.uuid4())

    assert await get_last_cycle(hub_id) is None

    store = LastCycleStore()
    snapshot = LastCycleSnapshot(
        hub_id=hub_id,
        at=datetime.now(timezone.utc),
        engine="stub_nearest_neighbor",
        duration_seconds=0.123,
        assigned_count=2,
        unassigned_count=1,
        over_budget=False,
    )
    await store.set(snapshot)

    fetched = await get_last_cycle(hub_id)
    assert fetched is not None
    assert fetched.assigned_count == 2
    assert fetched.engine == "stub_nearest_neighbor"


async def test_run_cycle_writes_last_cycle_snapshot(db_session, real_redis_client):
    """DispatchOptimizerService.run_cycle itself writes the snapshot, not
    just tests calling LastCycleStore directly - covers the actual wiring."""
    from app.optimizer.service import DispatchOptimizerService

    hub_id = str(uuid.uuid4())
    service = DispatchOptimizerService()
    result = await service.run_cycle(hub_id)  # no held orders/drivers - still a valid, empty cycle

    snapshot = await get_last_cycle(hub_id)
    assert snapshot is not None
    assert snapshot.engine == result.engine
    assert snapshot.assigned_count == 0
    assert snapshot.unassigned_count == 0


async def test_list_hubs_excludes_inactive_and_sorts_by_name(db_session):
    """Backs the dashboard's hub picker (docs/ROADMAP.md D1) - a raw UUID
    paste field until now, since no read endpoint existed for `hubs`."""
    db_session.add_all(
        [
            Hub(name="Zeta Hub", lat=34.05, lng=-118.25, active=True),
            Hub(name="Alpha Hub", lat=34.06, lng=-118.26, active=True),
            Hub(name="Inactive Hub", lat=34.07, lng=-118.27, active=False),
        ]
    )
    await db_session.commit()

    hubs = await list_hubs(session=db_session)
    assert [h.name for h in hubs] == ["Alpha Hub", "Zeta Hub"]
