"""Health/ops endpoints + the Dispatch Optimizer's manual trigger endpoint."""
from __future__ import annotations

from fastapi import APIRouter

from app.fleet_state.manager import FleetStateManager
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.optimizer import OptimizationResult

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/fleet/{hub_id}/drivers/state")
async def upsert_driver_state(hub_id: str, state: DriverState) -> dict:
    manager = FleetStateManager()
    await manager.upsert_driver_state(state)
    return {"ok": True}


@router.post("/fleet/{hub_id}/drivers/location")
async def upsert_driver_location(hub_id: str, location: DriverLocation) -> dict:
    manager = FleetStateManager()
    await manager.update_driver_location(location, hub_id)
    return {"ok": True}


@router.post("/optimizer/{hub_id}/run-cycle", response_model=OptimizationResult)
async def run_optimizer_cycle(hub_id: str) -> OptimizationResult:
    """
    Manually trigger one Dispatch Optimizer cycle for a hub. In production
    this is called on every "meaningful event" (new order released from
    hold, driver goes available, driver completes a stop) rather than
    polled - this endpoint exists for manual triggering, testing, and as
    the hook a scheduler/event-bus consumer would call into.
    """
    service = DispatchOptimizerService()
    return await service.run_cycle(hub_id)
