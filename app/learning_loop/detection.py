"""
Pattern detection for the Annotation and Learning Loop (component 6).

Drivers flag stops via `stop_flags` (app/models/stop.py). This module looks
for two specific, repeated flag types per shop and turns a strong enough
pattern into a proposed SLA hold-window adjustment for that shop:

  - HOLD_TOO_SHORT_FLAG: driver arrived and the shop wasn't ready yet
    (order was released from the hold queue too early) -> propose
    lengthening that shop's T2 hold window.
  - HOLD_TOO_LONG_FLAG: shop had been ready and waiting, order sat in the
    hold queue longer than it needed to -> propose shortening it.

Everything here is a pure function - no DB, no I/O - so it's cheap to unit
test and the DB-facing orchestration (app/learning_loop/service.py) stays
a thin wrapper around it.

CAVEAT (flag naming convention): `HOLD_TOO_SHORT_FLAG` /
`HOLD_TOO_LONG_FLAG` are proposed conventions for what the driver app
writes into `stop_flags.flag_type` - they are not yet agreed with whoever
builds the driver app (OS Shell, component 7, not started). Treat these
two string constants as the contract to finalize with that team, not as
already-shipped behavior.

CAVEAT (tuning): `HOLD_WINDOW_ADJUSTMENT_MINUTES`, `DEFAULT_MIN_OCCURRENCES`,
and the confidence formula are placeholders, in the same spirit as the SLA
engine's default hold windows (see app/sla/engine.py) - first things to
recalibrate against real Hub 1 driver annotations, not values to treat as
final.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

HOLD_TOO_SHORT_FLAG = "hold_window_too_short"
HOLD_TOO_LONG_FLAG = "hold_window_too_long"

RELEVANT_FLAG_TYPES = (HOLD_TOO_SHORT_FLAG, HOLD_TOO_LONG_FLAG)

# How much to adjust a shop's T2 hold window per detected pattern. Only T2
# (the standard tier) is adjusted - T1/T3 are deliberately left alone since
# a driver-perceived "too short/too long" complaint is about the routine
# case, not the urgent or flexible extremes.
HOLD_WINDOW_ADJUSTMENT_MINUTES = 10

# Minimum number of matching flags within the lookback window before a
# pattern is proposed at all.
DEFAULT_MIN_OCCURRENCES = 3

# Confidence never reaches 1.0 - a proposed_rule always needs a human to
# promote it to active_rules (see app/models/rules.py), no auto-approval
# path exists in Phase 1.
MAX_CONFIDENCE = 0.99


@dataclass(frozen=True)
class FlagRecord:
    """One stop_flags row, reduced to what detection needs."""

    shop_id: str
    flag_type: str
    created_at: datetime


@dataclass(frozen=True)
class DetectedPattern:
    shop_id: str
    flag_type: str
    occurrence_count: int
    confidence: float
    proposed_tier_minutes: dict[str, int]


def _confidence_for(occurrence_count: int, min_occurrences: int) -> float:
    """Scales up with more evidence; capped below 1.0 (see MAX_CONFIDENCE)."""
    ratio = occurrence_count / (min_occurrences * 2)
    return round(min(ratio, MAX_CONFIDENCE), 3)


def detect_patterns(
    flags: list[FlagRecord],
    current_minutes_by_shop: dict[str, dict[str, int]],
    *,
    min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
    adjustment_minutes: int = HOLD_WINDOW_ADJUSTMENT_MINUTES,
) -> list[DetectedPattern]:
    """
    `current_minutes_by_shop` maps shop_id -> that shop's current effective
    tier minutes (e.g. {"T1": 10, "T2": 45, "T3": 120}), already resolved by
    the caller from active_rules + defaults - this function has no opinion
    on where that number came from, only on how to adjust it.

    Shops with both flag types meeting the threshold in the same cycle are
    a conflicting signal (some drivers say "too early", others "too late")
    and are skipped rather than guessed at - a human should look at those
    directly rather than have the loop pick a side.
    """
    counts: dict[tuple[str, str], int] = {}
    for flag in flags:
        if flag.flag_type not in RELEVANT_FLAG_TYPES:
            continue
        key = (flag.shop_id, flag.flag_type)
        counts[key] = counts.get(key, 0) + 1

    shops_with_signal: dict[str, set[str]] = {}
    for shop_id, flag_type in counts:
        if counts[(shop_id, flag_type)] >= min_occurrences:
            shops_with_signal.setdefault(shop_id, set()).add(flag_type)

    patterns: list[DetectedPattern] = []
    for shop_id, flag_types in shops_with_signal.items():
        if len(flag_types) > 1:
            continue  # conflicting signal - skip, let a human look

        flag_type = next(iter(flag_types))
        occurrence_count = counts[(shop_id, flag_type)]
        current_minutes = dict(current_minutes_by_shop.get(shop_id, {}))
        current_t2 = current_minutes.get("T2")
        if current_t2 is None:
            continue  # no baseline to adjust from - skip rather than guess

        delta = adjustment_minutes if flag_type == HOLD_TOO_SHORT_FLAG else -adjustment_minutes
        proposed_t2 = max(current_t2 + delta, 0)

        patterns.append(
            DetectedPattern(
                shop_id=shop_id,
                flag_type=flag_type,
                occurrence_count=occurrence_count,
                confidence=_confidence_for(occurrence_count, min_occurrences),
                proposed_tier_minutes={**current_minutes, "T2": proposed_t2},
            )
        )

    return patterns
