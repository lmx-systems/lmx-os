"""
Batch-Hold Queue (component 3).

Holds classified orders briefly so the Dispatch Optimizer can commingle
nearby orders into one stop instead of dispatching one order at a time -
this is the mechanism the design doc credits for the DPH advantage over
single-order dispatch incumbents.

NOTE ON SOURCE OF TRUTH: the canonical decision-logic spec is now confirmed
(docs/ROADMAP.md B3/E4) - "LMX_OS_Tech_Strategy_and_Design.docx", Section 6
("Component 3 - Batch-Hold Queue")'s "four questions evaluated per order on
each cycle", sourced via the Source of Truth Index. The real four questions
are: (1) has the hold deadline been reached, (2) is there a geographic
cluster within the default 0.8mi radius, (3) is a driver already heading
this direction with capacity, (4) would dispatching now break another
optimization (create a conflict for a higher-priority order arriving
soon). Below replaces an earlier placeholder interpretation whose fourth
question was a fabricated absolute hold-time cap, not sourced from
anything - real question 4 is a genuine conflict-avoidance check, not a
timer, and is no longer needed as a separate safety net now that each
tier's SLA hold_deadline (question 1) is itself the real, spec-confirmed
ceiling on how long an order can be held.

The per-cycle questions, evaluated in order for every held order:
  0. Is this order HOT_SHOT? -> if yes, release immediately. Phase 8:
     HOT_SHOT is direct point-to-point and must never be commingled with
     another order's pickup (see accept_offer in app/api/driver_routes.py),
     so there is no clustering benefit to holding it at all - waiting for a
     cluster-mate that can never be paired with it only adds latency to
     the tier Sourabh is charging a premium for. This intentionally skips
     even the "no available drivers" check below: releasing costs nothing
     beyond moving the order from held to queued, ready to be picked up
     the moment a driver is free. Not one of the four canonical questions -
     a local addition this codebase needed once Phase 8 introduced the
     tier, predating the confirmed spec.
  1. (Question 1) Is this order past its SLA hold_deadline? -> if yes,
     force-release now, no matter what clustering looks like. SLA always
     wins.
  2. Is there currently no available driver at the hub at all? -> if yes,
     releasing wouldn't lead to a dispatch anyway, so keep holding
     regardless of clustering (avoids releasing into a queue with nothing
     to assign to). A prerequisite underlying question 3, not one of the
     four questions itself - if there is no driver at all, trivially none
     is "heading this direction" either.
  3. (Question 4) Would dispatching this order right now risk stranding a
     more urgent, still-held order that's about to need this same scarce
     driver supply? Only a real risk when driver availability is already
     tight (<=1 available) - with drivers to spare, dispatching this order
     doesn't cost the other one anything. See _would_conflict_with_a_more_
     urgent_order for the exact rule.
  4. (Question 2) Is there at least one other held order within the
     cluster radius? -> if yes, this order is a commingling candidate;
     keep holding.

Question 3 ("is a driver already heading this direction with capacity, add
to route") is not implemented in this function - it requires real-time
fleet position/route data, which this pure, no-I/O evaluator deliberately
doesn't have access to (see docs/ARCHITECTURE.md's engineering-decisions
list on why the SLA/hold-queue domain layer stays I/O-free). The closest
existing equivalent lives one layer up, in the Dispatch Optimizer's
mid-route insertion logic (app/optimizer/service.py) - a released order can
still be added to an already-active driver's route there.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.batch_queue.clustering import cluster_members
from app.config import settings


@dataclass(frozen=True)
class HeldOrder:
    order_id: str
    shop_lat: float
    shop_lng: float
    sla_tier: str
    hold_deadline: datetime
    held_since: datetime
    # Display-only - not used by any clustering/release decision below.
    # Defaults to "" rather than being required so existing callers/tests
    # that only care about the decision logic don't need updating.
    shop_name: str = ""


@dataclass(frozen=True)
class BatchDecision:
    order_id: str
    action: str  # "release" | "keep_holding"
    reason: str
    cluster_mate_ids: list[str]


_TIER_URGENCY = {"HOT_SHOT": 0, "T1": 1, "T2": 2, "T3": 3}  # lower = more urgent

# How soon a more-urgent order's hold_deadline has to be for it to count as
# "about to need a driver" for question 4's conflict check - not itself one
# of the four canonical questions' parameters (the spec names the check,
# not a specific threshold), but a value has to be picked for the check to
# be evaluable at all. 10 minutes matches this tier system's tightest real
# window (T1's own 8-minute max hold, docs/ROADMAP.md E5) plus a small
# margin, rather than an arbitrary round number.
CONFLICT_RISK_WINDOW_MINUTES = 10


def _would_conflict_with_a_more_urgent_order(
    order: HeldOrder,
    other_held_orders: list[HeldOrder],
    cluster_mate_ids: list[str],
    *,
    available_driver_count: int,
    now: datetime,
) -> bool:
    """Question 4: dispatching `order` now is only a real risk to another
    held order when there's at most one driver to go around - with spare
    capacity, sending this order costs the more urgent one nothing. Among
    non-cluster-mates (a cluster-mate would be released together with this
    order, not competing with it for a driver), a more urgent order whose
    own hold_deadline is imminent is the one this order could strand."""
    if available_driver_count > 1:
        return False

    threshold = now + timedelta(minutes=CONFLICT_RISK_WINDOW_MINUTES)
    this_urgency = _TIER_URGENCY.get(order.sla_tier, _TIER_URGENCY["T3"])
    return any(
        other.order_id not in cluster_mate_ids
        and _TIER_URGENCY.get(other.sla_tier, _TIER_URGENCY["T3"]) < this_urgency
        and other.hold_deadline <= threshold
        for other in other_held_orders
        if other.order_id != order.order_id
    )


def evaluate_held_order(
    order: HeldOrder,
    other_held_orders: list[HeldOrder],
    *,
    available_driver_count: int,
    now: datetime,
    cluster_radius_miles: float | None = None,
) -> BatchDecision:
    radius = cluster_radius_miles or settings.batch_hold_cluster_radius_miles

    # Question 0: HOT_SHOT never waits to pair with a cluster-mate - see
    # the module docstring.
    if order.sla_tier == "HOT_SHOT":
        return BatchDecision(
            order_id=order.order_id,
            action="release",
            reason="hot_shot_immediate_release",
            cluster_mate_ids=[],
        )

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

    # Prerequisite underlying question 3: nothing to dispatch to, so holding
    # costs nothing extra - keep holding regardless of clustering.
    if available_driver_count == 0:
        return BatchDecision(
            order_id=order.order_id,
            action="keep_holding",
            reason="no_available_drivers",
            cluster_mate_ids=cluster_mate_ids,
        )

    # Question 4: dispatching now would strand a more urgent order.
    if _would_conflict_with_a_more_urgent_order(
        order, other_held_orders, cluster_mate_ids, available_driver_count=available_driver_count, now=now
    ):
        return BatchDecision(
            order_id=order.order_id,
            action="keep_holding",
            reason="would_conflict_with_higher_priority_order",
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
