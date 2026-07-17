"""
Batch-Hold Queue (component 3).

Holds classified orders briefly so the Dispatch Optimizer can commingle
nearby orders into one stop instead of dispatching one order at a time -
this is the mechanism the design doc credits for the DPH advantage over
single-order dispatch incumbents.

NOTE ON SOURCE OF TRUTH: the canonical decision-logic spec lives in the
Google Drive "Source of Truth Index" (LMX OS Brief v1.0-1.2), which is not
in this local project cache. What follows is a best-effort, clearly-labeled
implementation of the "0.8-mile default clustering radius + 4-question
per-cycle decision logic" described in the peer review, structured so each
question is isolated and swappable once the canonical spec is confirmed.
Treat the four questions below as an interpretation to validate, not as
already-approved business logic.

The four per-cycle questions, evaluated in order for every held order:
  1. Is this order past its SLA hold_deadline? -> if yes, force-release now,
     no matter what clustering looks like. SLA always wins.
  2. Is there at least one other held order within the cluster radius?
     -> if yes, this order is a commingling candidate; keep holding unless
     rule 4 also fires.
  3. Is there currently no available driver at the hub at all? -> if yes,
     releasing wouldn't lead to a dispatch anyway, so keep holding
     regardless of clustering (avoids releasing into a queue with nothing
     to assign to).
  4. Has this order already been held past an absolute safety cap
     (independent of its SLA tier)? -> if yes, force-release even if it
     has cluster-mates, so a bad clustering match can never hold an order
     indefinitely.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.batch_queue.clustering import cluster_members
from app.config import settings

# Absolute safety cap - question 4. Independent of SLA tier; exists purely
# so clustering logic can never hold an order longer than this no matter
# what else is going on.
MAX_ABSOLUTE_HOLD_MINUTES = 30


@dataclass(frozen=True)
class HeldOrder:
    order_id: str
    shop_lat: float
    shop_lng: float
    sla_tier: str
    hold_deadline: datetime
    held_since: datetime


@dataclass(frozen=True)
class BatchDecision:
    order_id: str
    action: str  # "release" | "keep_holding"
    reason: str
    cluster_mate_ids: list[str]


def evaluate_held_order(
    order: HeldOrder,
    other_held_orders: list[HeldOrder],
    *,
    available_driver_count: int,
    now: datetime,
    cluster_radius_miles: float | None = None,
    max_absolute_hold_minutes: int = MAX_ABSOLUTE_HOLD_MINUTES,
) -> BatchDecision:
    radius = cluster_radius_miles or settings.batch_hold_cluster_radius_miles

    # Question 1: SLA deadline always wins.
    if now >= order.hold_deadline:
        return BatchDecision(
            order_id=order.order_id,
            action="release",
            reason="sla_hold_deadline_reached",
            cluster_mate_ids=[],
        )

    candidates = [
        (o.order_id, o.shop_lat, o.shop_lng) for o in other_held_orders if o.order_id != order.order_id
    ]
    cluster_mate_ids = cluster_members(order.shop_lat, order.shop_lng, candidates, radius)

    # Question 3: nothing to dispatch to, so holding costs nothing extra -
    # keep holding regardless of clustering.
    if available_driver_count == 0:
        return BatchDecision(
            order_id=order.order_id,
            action="keep_holding",
            reason="no_available_drivers",
            cluster_mate_ids=cluster_mate_ids,
        )

    # Question 4: absolute safety cap overrides a good cluster match.
    held_minutes = (now - order.held_since).total_seconds() / 60
    if held_minutes >= max_absolute_hold_minutes:
        return BatchDecision(
            order_id=order.order_id,
            action="release",
            reason="absolute_hold_cap_reached",
            cluster_mate_ids=cluster_mate_ids,
        )

    # Question 2: a good cluster match is worth continuing to hold for.
    if cluster_mate_ids:
        return BatchDecision(
            order_id=order.order_id,
            action="keep_holding",
            reason="cluster_mate_found",
            cluster_mate_ids=cluster_mate_ids,
        )

    return BatchDecision(
        order_id=order.order_id,
        action="release",
        reason="no_cluster_mate_and_drivers_available",
        cluster_mate_ids=[],
    )


def run_hold_cycle(
    held_orders: list[HeldOrder],
    *,
    available_driver_count: int,
    now: datetime | None = None,
    cluster_radius_miles: float | None = None,
) -> list[BatchDecision]:
    """Evaluate every currently-held order for one dispatch cycle."""
    reference_time = now or datetime.utcnow()
    return [
        evaluate_held_order(
            order,
            held_orders,
            available_driver_count=available_driver_count,
            now=reference_time,
            cluster_radius_miles=cluster_radius_miles,
        )
        for order in held_orders
    ]
