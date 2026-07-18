"""
Health/ops endpoints + manual trigger endpoints for the Dispatch Optimizer
and the Learning Loop's nightly job.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.store import HoldQueueStore
from app.db import get_db
from app.fleet_state.manager import FleetStateManager
from app.learning_loop.service import run_nightly_job
from app.models.order import Order
from app.optimizer.event_trigger import dispatch_event_bus
from app.optimizer.service import DispatchOptimizerService
from app.schemas.batch_queue import HeldOrderView
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.learning_loop import NightlyJobResult, ProposedRuleSummary
from app.schemas.optimizer import OptimizationResult
from app.schemas.order import OrderStatusSummary

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/fleet/{hub_id}/drivers/state")
async def upsert_driver_state(hub_id: str, state: DriverState) -> dict:
    manager = FleetStateManager()
    await manager.upsert_driver_state(state)
    # A status change (available/en_route/off_shift/on_break) changes what
    # the optimizer can assign - a raw location ping (below) doesn't, so
    # only this endpoint publishes.
    await dispatch_event_bus.publish(hub_id, "driver_status_changed")
    return {"ok": True}


@router.post("/fleet/{hub_id}/drivers/location")
async def upsert_driver_location(hub_id: str, location: DriverLocation) -> dict:
    manager = FleetStateManager()
    await manager.update_driver_location(location, hub_id)
    return {"ok": True}


@router.get("/fleet/{hub_id}/drivers", response_model=list[DriverState])
async def list_fleet_overview(hub_id: str) -> list[DriverState]:
    """
    Full driver roster for a hub - available, en_route, on_break, and
    off_shift alike. Built for the orchestrator dashboard; the optimizer
    itself only ever reads the narrower available-drivers view
    (FleetStateManager.get_fleet_snapshot).
    """
    manager = FleetStateManager()
    return await manager.get_fleet_overview(hub_id)


@router.get("/batch-queue/{hub_id}/held-orders", response_model=list[HeldOrderView])
async def list_held_orders(hub_id: str) -> list[HeldOrderView]:
    """Everything currently sitting in the Batch-Hold Queue for a hub."""
    store = HoldQueueStore()
    held = await store.get_all(hub_id)
    return [
        HeldOrderView(
            order_id=o.order_id,
            shop_lat=o.shop_lat,
            shop_lng=o.shop_lng,
            sla_tier=o.sla_tier,
            hold_deadline=o.hold_deadline,
            held_since=o.held_since,
        )
        for o in held
    ]


@router.get("/orders/{hub_id}/summary", response_model=OrderStatusSummary)
async def get_order_status_summary(
    hub_id: str, session: AsyncSession = Depends(get_db)
) -> OrderStatusSummary:
    """Order counts by status for a hub - dashboard quick-glance widget."""
    result = await session.execute(
        select(Order.status, func.count())
        .where(Order.hub_id == uuid.UUID(hub_id))
        .group_by(Order.status)
    )
    counts = {status.value: count for status, count in result.all()}
    return OrderStatusSummary(hub_id=hub_id, counts=counts)


@router.post("/optimizer/{hub_id}/run-cycle", response_model=OptimizationResult)
async def run_optimizer_cycle(hub_id: str) -> OptimizationResult:
    """
    Manually trigger one Dispatch Optimizer cycle for a hub. Real cycles
    are now event-triggered (see app/optimizer/event_trigger.py) off order
    ingestion and driver status changes rather than polled - this endpoint
    remains for manual triggering, testing, and ops (e.g. forcing a cycle
    after an out-of-band fleet-state fix).
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
