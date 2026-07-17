"""
Redis-backed component tests using fakeredis instead of a live Redis
server, so these run in CI without external services. Real Redis behavior
(pipelines, hashes, sets) is faithfully emulated by fakeredis for the
subset of commands we use here.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fakeredis import aioredis as fakeredis_aioredis

import app.batch_queue.store as hold_queue_store_module
import app.fleet_state.manager as fleet_state_manager_module
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.fleet_state.manager import FleetStateManager
from app.schemas.fleet import DriverLocation, DriverState


@pytest.fixture
def fake_redis(monkeypatch):
    # Both modules import `get_client` by name at module load time (`from
    # app.redis_client import get_client`), so the patch has to target each
    # module's local binding rather than app.redis_client itself.
    client = fakeredis_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(fleet_state_manager_module, "get_client", lambda: client)
    monkeypatch.setattr(hold_queue_store_module, "get_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_fleet_state_upsert_and_read_roundtrip(fake_redis):
    manager = FleetStateManager()
    state = DriverState(
        driver_id="d1", hub_id="hub-1", status="available", capacity_units=10, load_units=2
    )
    await manager.upsert_driver_state(state)

    fetched = await manager.get_driver_state("hub-1", "d1")
    assert fetched == state


@pytest.mark.asyncio
async def test_available_drivers_set_tracks_status_changes(fake_redis):
    manager = FleetStateManager()
    await manager.upsert_driver_state(
        DriverState(driver_id="d1", hub_id="hub-1", status="available", capacity_units=10)
    )
    assert await manager.get_available_driver_ids("hub-1") == ["d1"]

    await manager.upsert_driver_state(
        DriverState(driver_id="d1", hub_id="hub-1", status="en_route", capacity_units=10)
    )
    assert await manager.get_available_driver_ids("hub-1") == []


@pytest.mark.asyncio
async def test_driver_location_roundtrip(fake_redis):
    manager = FleetStateManager()
    location = DriverLocation(driver_id="d1", lat=34.05, lng=-118.25, recorded_at="2026-07-17T12:00:00Z")
    await manager.update_driver_location(location, "hub-1")
    fetched = await manager.get_driver_location("hub-1", "d1")
    assert fetched == location


@pytest.mark.asyncio
async def test_fleet_snapshot_returns_only_available_drivers(fake_redis):
    manager = FleetStateManager()
    await manager.upsert_driver_state(
        DriverState(driver_id="d1", hub_id="hub-1", status="available", capacity_units=10, load_units=1)
    )
    await manager.upsert_driver_state(
        DriverState(driver_id="d2", hub_id="hub-1", status="off_shift", capacity_units=10)
    )
    snapshot = await manager.get_fleet_snapshot("hub-1")
    assert [d.driver_id for d in snapshot] == ["d1"]


@pytest.mark.asyncio
async def test_hold_queue_store_add_get_remove_roundtrip(fake_redis):
    store = HoldQueueStore()
    now = datetime.now(timezone.utc)
    order = HeldOrder(
        order_id="o1",
        shop_lat=34.05,
        shop_lng=-118.25,
        sla_tier="T2",
        hold_deadline=now + timedelta(minutes=30),
        held_since=now,
    )
    await store.add("hub-1", order)
    all_held = await store.get_all("hub-1")
    assert len(all_held) == 1
    assert all_held[0].order_id == "o1"

    await store.remove("hub-1", "o1")
    assert await store.get_all("hub-1") == []
