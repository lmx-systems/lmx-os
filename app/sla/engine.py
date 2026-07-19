"""
Dynamic SLA Engine (component 2).

Classifies every incoming order into an urgency tier (T1/T2/T3) and computes
a hold-deadline: the latest moment the Batch-Hold Queue is allowed to keep
holding this order before it must be released for routing, even without a
good clustering match.

IMPORTANT - these default hold windows are placeholders, not validated
numbers. Per the peer review, the 2.5 DPH target is "a flat assertion...
should be reframed as 'in our model, proving it live at Hub 1'." Do not
treat the constants below as gospel; they exist so the pipeline is
end-to-end runnable, and are the first thing to recalibrate against real
Hub 1 data. Per-shop/per-hub overrides live in the active_rules table
(rule_type='sla_hold_window_override') and always win over these defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.schemas.order import ClassifiedOrder, NormalizedOrder

# Default hold windows in minutes, keyed by tier. Placeholder values pending
# Hub 1 calibration - see module docstring. HOT_SHOT is intentionally near
# zero, not exactly zero - see resolve_hold_window_minutes/the Batch-Hold
# Queue (app/batch_queue/queue.py), which also bypasses the cluster-mate
# wait entirely for this tier so it never sits around for a pairing that
# should never happen anyway (Phase 8: HOT_SHOT is direct point-to-point,
# never commingled - see accept_offer in app/api/driver_routes.py).
DEFAULT_HOLD_WINDOW_MINUTES: dict[str, int] = {
    "HOT_SHOT": 2,
    "T1": 10,
    "T2": 45,
    "T3": 120,
}

# Keys inside NormalizedOrder.raw_payload that force a tier regardless of
# heuristics, when a POS adapter is able to surface them. Checked in order -
# HOT_SHOT wins over a plain rush flag if a client somehow sets both, since
# it's the more specific/urgent request (Phase 8: a premium, client-priced
# tier a client explicitly asks for per-customer, not just "urgent").
HOT_SHOT_FLAG_KEYS = ("hot_shot", "is_hot_shot")
RUSH_FLAG_KEYS = ("rush", "is_rush", "priority", "urgent")
SCHEDULED_FLAG_KEYS = ("scheduled_delivery", "will_call", "next_day")


@dataclass(frozen=True)
class HoldWindowOverride:
    """A per-shop or per-hub override sourced from active_rules."""

    scope_shop_id: str | None
    scope_hub_id: str | None
    tier_minutes: dict[str, int]


def _payload_flag_true(payload: dict, keys: tuple[str, ...]) -> bool:
    return any(bool(payload.get(k)) for k in keys)


def classify_tier(order: NormalizedOrder) -> tuple[str, str]:
    """
    Returns (tier, reason). Heuristic order:
      1. Explicit hot-shot flag from the POS payload -> HOT_SHOT.
      2. Explicit rush flag from the POS payload -> T1.
      3. Explicit scheduled/will-call flag -> T3.
      4. Otherwise -> T2 (the default, standard-urgency bucket).
    """
    if _payload_flag_true(order.raw_payload, HOT_SHOT_FLAG_KEYS):
        return "HOT_SHOT", "hot-shot flag present in source payload"

    if _payload_flag_true(order.raw_payload, RUSH_FLAG_KEYS):
        return "T1", "rush flag present in source payload"

    if _payload_flag_true(order.raw_payload, SCHEDULED_FLAG_KEYS):
        return "T3", "scheduled/will-call flag present in source payload"

    return "T2", "no urgency flags present; defaulted to standard tier"


def resolve_hold_window_minutes(
    tier: str, overrides: list[HoldWindowOverride] | None = None
) -> int:
    """
    Shop-level overrides win over hub-level overrides, which win over the
    hardcoded defaults. `overrides` is expected to already be filtered/sorted
    by the caller (most specific first) - this function just walks the list.
    """
    if overrides:
        for override in overrides:
            if tier in override.tier_minutes:
                return override.tier_minutes[tier]
    return DEFAULT_HOLD_WINDOW_MINUTES[tier]


def classify_order(
    order: NormalizedOrder,
    *,
    now: datetime | None = None,
    overrides: list[HoldWindowOverride] | None = None,
) -> ClassifiedOrder:
    """
    Pure function: no I/O. Callers (the ingestion service) are responsible
    for loading `overrides` from active_rules and persisting the result.
    """
    reference_time = now or order.requested_at
    tier, reason = classify_tier(order)
    hold_minutes = resolve_hold_window_minutes(tier, overrides)
    hold_deadline = reference_time + timedelta(minutes=hold_minutes)

    return ClassifiedOrder(
        order_id=order.external_order_ref,
        sla_tier=tier,
        hold_deadline=hold_deadline,
        reason=reason,
    )
