"""
Record what LMX OS would have done, and do nothing about it (docs/ROADMAP.md W9).

Session decision D3: every initial customer engagement runs live on the Elite EXTRA
scaffold while LMX OS decides in parallel on the same orders, and the two are compared
until a scorecard passes and that engagement cuts over. This module is the parallel
decision path - it calls the optimizer's `plan_cycle` and writes down the answer.

**Why this lives outside `app/optimizer/`.** The dispatch engine must not import an
edge package (`tests/test_architecture_boundaries.py` enforces it), and shadow mode is
edge: it is a reporting and comparison concern that reads the core's decisions. Hooking
a recorder inside `run_cycle` would have inverted that dependency. Running beside it
instead also means shadow mode cannot break dispatch, which matters when the entire
point is to run it against a customer's live operation.

**It changes nothing, and that is a property rather than a promise.** `plan_cycle`
performs no writes - no hold-queue removals, no order-status updates, no route offers,
no notifications. `tests/test_shadow_recorder.py` asserts a shadow run leaves the hold
queue and every order status exactly as it found them, because a shadow dispatcher that
quietly acted would be a second system fighting the real one for the same drivers.

**What it deliberately does not do: compare.** Divergence is computed later, by joining
these rows against what actually happened to the order. A comparison written here would
have to guess at outcomes that have not occurred yet - an order assigned in shadow at
09:04 has no real outcome until it is delivered or fails - and freezing a guess is worse
than recording nothing.
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shadow_decision import ShadowDecision, ShadowOrderDecision
from app.optimizer.service import DispatchOptimizerService
from app.schemas.optimizer import CyclePlan

logger = structlog.get_logger(__name__)


async def record_shadow_cycle(
    session: AsyncSession,
    hub_id: str,
    optimizer: DispatchOptimizerService | None = None,
) -> ShadowDecision:
    """Run one non-acting dispatch cycle for `hub_id` and persist what it decided."""
    optimizer = optimizer or DispatchOptimizerService()
    plan = await optimizer.plan_cycle(hub_id)
    return await persist_plan(session, plan)


async def persist_plan(session: AsyncSession, plan: CyclePlan) -> ShadowDecision:
    """Write a `CyclePlan` down as durable evidence.

    Split from `record_shadow_cycle` so the persistence can be tested against a plan
    built by hand, without standing up a fleet and a hold queue to produce one.
    """
    hub_uuid = uuid.UUID(plan.hub_id)

    assigned_order_ids: set[str] = {
        stop_id for assignment in plan.assignments for stop_id in assignment.stop_ids
    }

    decision = ShadowDecision(
        hub_id=hub_uuid,
        planned_at=plan.planned_at,
        hub_closed=plan.hub_closed,
        engine=plan.engine,
        plan_duration_seconds=plan.plan_duration_seconds,
        held_order_count=plan.held_order_count,
        released_order_count=len(plan.released_order_ids),
        driver_count=len(plan.drivers),
        assigned_order_count=len(assigned_order_ids),
        unassigned_order_count=len(plan.unassigned_stop_ids),
        assignment_payload=[
            {
                "driver_id": assignment.driver_id,
                "stop_ids": list(assignment.stop_ids),
                "visits": [
                    {"order_id": visit.order_id, "kind": visit.kind}
                    for visit in assignment.visits
                ],
            }
            for assignment in plan.assignments
        ],
    )
    session.add(decision)
    # The per-order rows carry the parent id, so the parent needs one before they are
    # built. Flush rather than commit - the caller owns the transaction, and a shadow
    # cycle that half-recorded would be worse than one that did not record at all.
    await session.flush()

    tier_by_order_id = {stop.stop_id: stop.sla_tier for stop in plan.stops}

    for assignment in plan.assignments:
        for index, stop_id in enumerate(assignment.stop_ids):
            session.add(
                ShadowOrderDecision(
                    shadow_decision_id=decision.id,
                    order_id=uuid.UUID(stop_id),
                    hub_id=hub_uuid,
                    planned_at=plan.planned_at,
                    decision="assigned",
                    driver_id=uuid.UUID(assignment.driver_id),
                    sequence_index=index,
                    sla_tier=tier_by_order_id.get(stop_id),
                )
            )

    for stop_id in plan.unassigned_stop_ids:
        # An order the optimizer released and then could not place. Recorded distinctly
        # from one it never released: "we would have left it held" produces no row,
        # because it is not a dispatch decision anyone can be wrong about yet.
        session.add(
            ShadowOrderDecision(
                shadow_decision_id=decision.id,
                order_id=uuid.UUID(stop_id),
                hub_id=hub_uuid,
                planned_at=plan.planned_at,
                decision="unassigned",
                driver_id=None,
                sequence_index=None,
                sla_tier=tier_by_order_id.get(stop_id),
            )
        )

    logger.info(
        "shadow_cycle_recorded",
        hub_id=plan.hub_id,
        hub_closed=plan.hub_closed,
        engine=plan.engine,
        assigned=len(assigned_order_ids),
        unassigned=len(plan.unassigned_stop_ids),
        plan_seconds=plan.plan_duration_seconds,
    )
    return decision
