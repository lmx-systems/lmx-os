"""
Confirms the two real event producers (driver status changes, order
ingestion) actually publish to the Dispatch Optimizer's event bus - see
app/optimizer/event_trigger.py and docs/ARCHITECTURE.md next-steps item 6.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.routes as routes_module
import app.ingestion.router as ingestion_router_module
from app.schemas.fleet import DriverState


@pytest.mark.asyncio
async def test_driver_state_upsert_publishes_status_changed_event(monkeypatch):
    fake_manager = AsyncMock()
    monkeypatch.setattr(routes_module, "FleetStateManager", lambda: fake_manager)
    publish_mock = AsyncMock()
    monkeypatch.setattr(routes_module.dispatch_event_bus, "publish", publish_mock)

    state = DriverState(driver_id="d1", hub_id="hub-1", status="available", capacity_units=5)
    await routes_module.upsert_driver_state("hub-1", state)

    fake_manager.upsert_driver_state.assert_awaited_once_with(state)
    publish_mock.assert_awaited_once_with("hub-1", "driver_status_changed")


@pytest.mark.asyncio
async def test_driver_location_update_does_not_publish_an_event(monkeypatch):
    # A raw GPS ping doesn't change what the optimizer can assign - only a
    # status change does - so this endpoint must stay silent on the bus.
    fake_manager = AsyncMock()
    monkeypatch.setattr(routes_module, "FleetStateManager", lambda: fake_manager)
    publish_mock = AsyncMock()
    monkeypatch.setattr(routes_module.dispatch_event_bus, "publish", publish_mock)

    from app.schemas.fleet import DriverLocation

    location = DriverLocation(driver_id="d1", lat=1.0, lng=2.0, recorded_at="2026-07-18T00:00:00Z")
    await routes_module.upsert_driver_location("hub-1", location)

    publish_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_order_endpoint_publishes_order_held_event(monkeypatch):
    fake_order = SimpleNamespace(id="o1", sla_tier="T2", hold_deadline=None, status="held")
    ingest_mock = AsyncMock(return_value=fake_order)
    monkeypatch.setattr(ingestion_router_module, "ingest_order", ingest_mock)
    publish_mock = AsyncMock()
    monkeypatch.setattr(ingestion_router_module.dispatch_event_bus, "publish", publish_mock)

    result = await ingestion_router_module.ingest_order_endpoint(
        "hub-1", "client-1", "epicor", {"foo": "bar"}, session=object(), hold_queue=object()
    )

    assert result["order_id"] == "o1"
    publish_mock.assert_awaited_once_with("hub-1", "order_held")
