"""
Health/ops endpoints + manual trigger endpoints for the Dispatch Optimizer
and the Learning Loop's nightly job.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.clustering import cluster_members
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.db import get_db
from app.fleet_state.manager import FleetStateManager
from app import metrics
from app.learning_loop.service import run_nightly_job
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order
from app.ops_auth.dependencies import AuthedOpsUser, require_admin
from app.optimizer.event_trigger import dispatch_event_bus
from app.optimizer.last_cycle_store import LastCycleStore
from app.optimizer.service import DispatchOptimizerService
from app.reporting.lmx_link import build_scorecard
from app.schemas.batch_queue import HeldOrderView
from app.schemas.reporting import LinkScorecardView, MeasurementView
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.hub import HubSummary
from app.schemas.learning_loop import NightlyJobResult, ProposedRuleSummary
from app.schemas.optimizer import LastCycleSnapshot, OptimizationResult
from app.schemas.order import OrderStatusSummary

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus scrape target (docs/ROADMAP.md S4, app/metrics.py).
    Exempt from the ops-JWT gate (a scraper has no per-user login) - see
    OpsUserAuthMiddleware.EXEMPT_PATHS and app/metrics.py's auth note on
    restricting this to a private scrape network in production."""
    payload, content_type = metrics.render_latest()
    return Response(content=payload, media_type=content_type)


@router.get("/lmx-link/scorecard", response_model=LinkScorecardView)
async def lmx_link_scorecard(
    session: AsyncSession = Depends(get_db),
    _admin: AuthedOpsUser = Depends(require_admin),
) -> LinkScorecardView:
    """LMX Link's success metrics, computed from real rows (LMX_LINK_PLAN §3.4).

    Those five targets have been quoted in updates since the plan was written and none
    of them was answerable. Three now are; the other two say why not, in the response,
    rather than being dropped or filled with something that looks like a number.

    Computed on request rather than exported as Prometheus counters, for the same
    reason `app/health/checks.py` evaluates server-side: per-process counters reset on
    cold start and differ per instance on an autoscaled deployment. These are
    distributions over durable rows.
    """
    scorecard = await build_scorecard(session)
    return LinkScorecardView(
        generated_at=scorecard.generated_at,
        measurements=[
            MeasurementView(
                name=m.name,
                target=m.target,
                unit=m.unit,
                median=m.median,
                p90=m.p90,
                sample_size=m.sample_size,
                not_measured=m.not_measured,
            )
            for m in scorecard.measurements
        ],
    )


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
    join and the location fetch below live here and not in
    FleetStateManager/DriverState's Redis round-trip - the hot path has no
    reason to pay for either.

    Last reported position is included (docs/ROADMAP.md F1) so the
    dashboard can place drivers on a map (F2) rather than only listing
    their assigned stops. A driver who has never reported one comes back
    with lat/lng null, which is also precisely why the optimizer would skip
    them - so this view doubles as the diagnostic for "why is nobody being
    assigned work."
    """
    manager = FleetStateManager()
    roster = await manager.get_fleet_overview(hub_id)
    if not roster:
        return roster

    driver_ids = [uuid.UUID(d.driver_id) for d in roster]
    result = await session.execute(select(Driver.id, Driver.name).where(Driver.id.in_(driver_ids)))
    names = {str(driver_id): name for driver_id, name in result.all()}

    # One Redis read per driver: location lives under its own per-driver key,
    # not in the state hash get_fleet_overview already bulk-read. Gathered
    # concurrently so a full roster costs one round-trip's latency rather
    # than one per driver.
    locations = await asyncio.gather(
        *(manager.get_driver_location(hub_id, d.driver_id) for d in roster)
    )

    for driver, location in zip(roster, locations):
        driver.name = names.get(driver.driver_id)
        if location is not None:
            driver.lat = location.lat
            driver.lng = location.lng
            driver.location_recorded_at = location.recorded_at
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
