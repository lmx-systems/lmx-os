"""
Same shapes as tests/test_fleet_state_and_hold_queue.py, but against a real
Redis instead of fakeredis - catches anything fakeredis emulates
differently from the real thing (pipeline semantics, type coercion, etc).
"""
import pytest

from app.fleet_state.manager import FleetStateManager
from app.schemas.fleet import DriverLocation, DriverState

pytestmark = pytest.mark.integration


async def test_fleet_state_upsert_and_read_roundtrip(real_redis_client):
    manager = FleetStateManager()
    state = DriverState(
        driver_id="d1", hub_id="hub-int", status="available", capacity_units=10, load_units=2
    )
    await manager.upsert_driver_state(state)

    fetched = await manager.get_driver_state("hub-int", "d1")
    assert fetched == state


async def test_available_drivers_set_tracks_status_changes(real_redis_client):
    manager = FleetStateManager()
    await manager.upsert_driver_state(
        DriverState(driver_id="d1", hub_id="hub-int", status="available", capacity_units=10)
    )
    assert await manager.get_available_driver_ids("hub-int") == ["d1"]

    await manager.upsert_driver_state(
        DriverState(driver_id="d1", hub_id="hub-int", status="en_route", capacity_units=10)
    )
    assert await manager.get_available_driver_ids("hub-int") == []


async def test_fleet_overview_includes_off_shift_drivers(real_redis_client):
    manager = FleetStateManager()
    await manager.upsert_driver_state(
        DriverState(driver_id="d1", hub_id="hub-int", status="available", capacity_units=10)
    )
    await manager.upsert_driver_state(
        DriverState(driver_id="d2", hub_id="hub-int", status="off_shift", capacity_units=10)
    )
    overview = await manager.get_fleet_overview("hub-int")
    assert {d.driver_id for d in overview} == {"d1", "d2"}


async def test_driver_location_roundtrip(real_redis_client):
    manager = FleetStateManager()
    location = DriverLocation(driver_id="d1", lat=34.05, lng=-118.25, recorded_at="2026-07-17T12:00:00Z")
    await manager.update_driver_location(location, "hub-int")

    fetched = await manager.get_driver_location("hub-int", "d1")
    assert fetched == location


async def test_fleet_snapshot_returns_only_available_drivers(real_redis_client):
    manager = FleetStateManager()
    await manager.upsert_driver_state(
        DriverState(driver_id="d1", hub_id="hub-int", status="available", capacity_units=10, load_units=1)
    )
    await manager.upsert_driver_state(
        DriverState(driver_id="d2", hub_id="hub-int", status="off_shift", capacity_units=10)
    )
    snapshot = await manager.get_fleet_snapshot("hub-int")
    assert [d.driver_id for d in snapshot] == ["d1"]
