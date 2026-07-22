"""
Health/ops endpoints + manual trigger endpoints for the Dispatch Optimizer
and the Learning Loop's nightly job.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.clustering import cluster_members
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.db import get_db
from app.fleet_state.manager import FleetStateManager
from app.learning_loop.service import run_nightly_job
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order
from app.ops_auth.dependencies import AuthedOpsUser, require_admin
from app.optimizer.event_trigger import dispatch_event_bus
from app.optimizer.last_cycle_store import LastCycleStore
from app.optimizer.service import DispatchOptimizerService
from app.schemas.batch_queue import HeldOrderView
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.hub import HubSummary
from app.schemas.learning_loop import NightlyJobResult, ProposedRuleSummary
from app.schemas.optimizer import LastCycleSnapshot, OptimizationResult
from app.schemas.order import OrderStatusSummary

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/hubs", response_model=list[HubSummary])
async def list_hubs(session: AsyncSession = Depends(get_db)) -> list[HubSummary]:
    """Backs the dashboard's hub picker (docs/ROADMAP.md D1) - hub
    selection was a raw UUID paste field until now, since no read endpoint
    existed for the `hubs` table at all. Excludes inactive hubs - nothing
    in ops tooling should be able to select one to act on."""
    result = await session.execute(select(Hub).where(Hub.active.is_(True)).order_by(Hub.name))
    return [HubSummary(hub_id=str(hub.id), name=hub.name) for hub in result.scalars().all()]


@router.post("/fleet/{hub_id}/drivers/state")
async def upsert_driver_state(
    hub_id: str, state: DriverState, _admin: AuthedOpsUser = Depends(require_admin)
) -> dict:
    manager = FleetStateManager()
    await manager.upsert_driver_state(state)
    # A status change (available/en_route/off_shift/on_break) changes what
    # the optimizer can assign - a raw location ping (below) doesn't, so
    # only this endpoint publishes.
    await dispatch_event_bus.publish(hub_id, "driver_status_changed")
    return {"ok": True}


@router.post("/fleet/{hub_id}/drivers/location")
async def upsert_driver_location(
    hub_id: str, location: DriverLocation, _admin: AuthedOpsUser = Depends(require_admin)
) -> dict:
    manager = FleetStateManager()
    await manager.update_driver_location(location, hub_id)
    return {"ok": True}


@router.get("/fleet/{hub_id}/drivers", response_model=list[DriverState])
async def list_fleet_overview(hub_id: str, session: AsyncSession = Depends(get_db)) -> list[DriverState]:
    """
    Full driver roster for a hub - available, en_route, on_break, and
    off_shift alike. Built for the orchestrator dashboard; the optimizer
    itself only ever reads the narrower available-drivers view
    (FleetStateManager.get_fleet_snapshot), which is why the display name
    join below lives here and not in FleetStateManager/DriverState's Redis
    round-trip - the hot path has no reason to pay for it.
    """
    manager = FleetStateManager()
    roster = await manager.get_fleet_overview(hub_id)
    if not roster:
        return roster

    driver_ids = [uuid.UUID(d.driver_id) for d in roster]
    result = await session.execute(select(Driver.id, Driver.name).where(Driver.id.in_(driver_ids)))
    names = {str(driver_id): name for driver_id, name in result.all()}

    for driver in roster:
        driver.name = names.get(driver.driver_id)
    return roster


@router.get("/batch-queue/{hub_id}/held-orders", response_model=list[HeldOrderView])
async def list_held_orders(hub_id: str) -> list[HeldOrderView]:
    """
    Everything currently sitting in the Batch-Hold Queue for a hub.
    cluster_mate_ids is computed fresh here from the same clustering logic
    the Dispatch Optimizer uses (app.batch_queue.clustering.cluster_members)
    against the rest of this response's rows - it isn't persisted, since
    it changes as soon as a sibling order is added/removed/released.
    """
    store = HoldQueueStore()
    held = await store.get_all(hub_id)
    radius = settings.batch_hold_cluster_radius_miles
    views: list[HeldOrderView] = []
    for order in held:
        candidates = [(o.order_id, o.shop_lat, o.shop_lng) for o in held if o.order_id != order.order_id]
        cluster_mate_ids = cluster_members(order.shop_lat, order.shop_lng, candidates, radius)
        views.append(
            HeldOrderView(
                order_id=order.order_id,
                shop_lat=order.shop_lat,
                shop_lng=order.shop_lng,
                sla_tier=order.sla_tier,
                hold_deadline=order.hold_deadline,
                held_since=order.held_since,
                shop_name=order.shop_name,
                cluster_mate_ids=cluster_mate_ids,
            )
        )
    return views


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
async def run_optimizer_cycle(hub_id: str, _admin: AuthedOpsUser = Depends(require_admin)) -> OptimizationResult:
    """
    Manually trigger one Dispatch Optimizer cycle for a hub. Real cycles
    are now event-triggered (see app/optimizer/event_trigger.py) off order
    ingestion and driver status changes rather than polled - this endpoint
    remains for manual triggering, testing, and ops (e.g. forcing a cycle
    after an out-of-band fleet-state fix). Admin-only (docs/ROADMAP.md S1) -
    a viewer can watch a cycle happen but not force one.
    """
    service = DispatchOptimizerService()
    return await service.run_cycle(hub_id)


@router.get("/optimizer/{hub_id}/last-cycle", response_model=LastCycleSnapshot | None)
async def get_last_cycle(hub_id: str) -> LastCycleSnapshot | None:
    """
    The most recently completed Dispatch Optimizer cycle for this hub,
    whether it was triggered manually or automatically off an event - see
    app/optimizer/last_cycle_store.py. Returns null if no cycle has run
    for this hub yet (e.g. a brand new hub, or right after a Redis flush).
    """
    store = LastCycleStore()
    return await store.get(hub_id)


@router.post("/learning-loop/{hub_id}/run-nightly-job", response_model=NightlyJobResult)
async def run_learning_loop_nightly_job(
    hub_id: str, session: AsyncSession = Depends(get_db), _admin: AuthedOpsUser = Depends(require_admin)
) -> NightlyJobResult:
    """
    Manually trigger the Learning Loop's pattern-detection job for a hub
    (component 6). In production this runs on a schedule (nightly, per the
    design doc) rather than on demand - this endpoint exists for manual
    triggering, testing, and as the hook a scheduler would call into.
    Admin-only (docs/ROADMAP.md S1).

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
