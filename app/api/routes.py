"""
Health/ops endpoints + manual trigger endpoints for the Dispatch Optimizer
and the Learning Loop's nightly job.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.fleet_state.manager import FleetStateManager
from app.learning_loop.service import run_nightly_job
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.learning_loop import NightlyJobResult, ProposedRuleSummary
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


@router.post("/learning-loop/{hub_id}/run-nightly-job", response_model=NightlyJobResult)
async def run_learning_loop_nightly_job(
    hub_id: str, session: AsyncSession = Depends(get_db)
) -> NightlyJobResult:
    """
    Manually trigger the Learning Loop's pattern-detection job for a hub
    (component 6). In production this runs on a schedule (nightly, per the
    design doc) rather than on demand - this endpoint exists for manual
    triggering, testing, and as the hook a scheduler would call into.

    Detected patterns become `proposed_rules` rows - nothing is
    auto-promoted to `active_rules`. A human reviews and promotes.
    """
    created = await run_nightly_job(session, hub_id=hub_id)
    return NightlyJobResult(
        hub_id=hub_id,
        proposals_created=[
            ProposedRuleSummary(
                proposed_rule_id=str(rule.id),
                shop_id=rule.scope.get("shop_id", ""),
                rule_type=rule.rule_type,
                proposed_change=rule.proposed_change,
                confidence=float(rule.confidence),
                supporting_annotation_count=rule.supporting_annotation_count,
            )
            for rule in created
        ],
    )
