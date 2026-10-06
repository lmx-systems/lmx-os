from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.order import NormalizedOrder
from app.sla.engine import (
    latest_safe_hold_deadline,
    DEFAULT_HOLD_WINDOW_MINUTES,
    HoldWindowOverride,
    TierOverride,
    classify_order,
    classify_tier,
    resolve_hold_window_minutes,
)


def make_order(**overrides) -> NormalizedOrder:
    defaults = dict(
        external_order_ref="ORD-1",
        source_system="flat_file",
        hub_id="hub-1",
        client_id="client-1",
        shop_external_ref="shop-1",
        shop_lat=34.05,
        shop_lng=-118.25,
        weight_units=1.0,
        requested_at=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
        raw_payload={},
    )
    defaults.update(overrides)
    return NormalizedOrder(**defaults)


def test_hot_shot_flag_forces_hot_shot_tier():
    order = make_order(raw_payload={"hot_shot": True})
    tier, reason = classify_tier(order)
    assert tier == "HOT_SHOT"
    assert "hot-shot" in reason


def test_hot_shot_flag_takes_priority_over_rush_flag():
    order = make_order(raw_payload={"hot_shot": True, "rush": True})
    tier, _ = classify_tier(order)
    assert tier == "HOT_SHOT"


def test_rush_flag_forces_t1():
    order = make_order(raw_payload={"rush": True})
    tier, reason = classify_tier(order)
    assert tier == "T1"
    assert "rush" in reason


def test_scheduled_flag_forces_t3():
    order = make_order(raw_payload={"will_call": True})
    tier, reason = classify_tier(order)
    assert tier == "T3"


def test_default_is_t2():
    order = make_order(raw_payload={})
    tier, _ = classify_tier(order)
    assert tier == "T2"


def test_rush_flag_takes_priority_over_scheduled_flag():
    order = make_order(raw_payload={"rush": True, "will_call": True})
    tier, _ = classify_tier(order)
    assert tier == "T1"


def test_hold_deadline_uses_default_window_when_no_override():
    order = make_order()
    classified = classify_order(order, now=order.requested_at)
    expected = order.requested_at + timedelta(minutes=DEFAULT_HOLD_WINDOW_MINUTES["T2"])
    assert classified.hold_deadline == expected


def test_shop_override_wins_over_hub_override_and_default():
    shop_override = HoldWindowOverride(
        scope_shop_id="shop-1", scope_hub_id=None, tier_minutes={"T2": 5}
    )
    hub_override = HoldWindowOverride(
        scope_shop_id=None, scope_hub_id="hub-1", tier_minutes={"T2": 999}
    )
    minutes = resolve_hold_window_minutes("T2", overrides=[shop_override, hub_override])
    assert minutes == 5


def test_missing_tier_in_overrides_falls_back_to_default():
    override = HoldWindowOverride(scope_shop_id="shop-1", scope_hub_id=None, tier_minutes={"T1": 1})
    minutes = resolve_hold_window_minutes("T2", overrides=[override])
    assert minutes == DEFAULT_HOLD_WINDOW_MINUTES["T2"]


# --- Orchestrator-editable urgency rules (docs/ROADMAP.md W6) ---

def test_tier_override_wins_over_the_payload_flag_heuristic():
    # "body panels are never urgent" - the ops rule downgrades even an order
    # the heuristic would call T1 (rush flag present).
    order = make_order(raw_payload={"part_category": "body_panel", "rush": True})
    overrides = [TierOverride(match_key="part_category", match_value="body_panel", tier="T3")]
    classified = classify_order(order, tier_overrides=overrides)
    assert classified.sla_tier == "T3"
    assert "orchestrator urgency rule" in classified.reason
    # And the hold window follows the *overridden* tier, not the heuristic one.
    assert classified.hold_deadline == order.requested_at + timedelta(
        minutes=DEFAULT_HOLD_WINDOW_MINUTES["T3"]
    )


def test_tier_override_matches_case_insensitively():
    order = make_order(raw_payload={"part_category": "Body Panel"})
    overrides = [TierOverride(match_key="part_category", match_value="body panel", tier="T3")]
    assert classify_order(order, tier_overrides=overrides).sla_tier == "T3"


def test_non_matching_tier_override_falls_through_to_the_heuristic():
    order = make_order(raw_payload={"part_category": "brake_pads", "rush": True})
    overrides = [TierOverride(match_key="part_category", match_value="body_panel", tier="T3")]
    assert classify_order(order, tier_overrides=overrides).sla_tier == "T1"  # rush heuristic


def test_first_matching_tier_override_wins():
    order = make_order(raw_payload={"part_category": "body_panel"})
    overrides = [
        TierOverride(match_key="part_category", match_value="body_panel", tier="T3"),
        TierOverride(match_key="part_category", match_value="body_panel", tier="T2"),
    ]
    assert classify_order(order, tier_overrides=overrides).sla_tier == "T3"


def test_no_tier_overrides_leaves_classification_unchanged():
    order = make_order(raw_payload={"rush": True})
    assert classify_order(order, tier_overrides=[]).sla_tier == "T1"
    assert classify_order(order).sla_tier == "T1"


def test_the_latest_safe_hold_deadline_is_the_design_docs_worked_example():
    """Section 5: "A T1 order with a 45-minute window going to a shop 15
    minutes away gets a hold deadline of now + 25 minutes." """
    now = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
    assert latest_safe_hold_deadline(now + timedelta(minutes=45), drive_minutes=15) == (
        now + timedelta(minutes=25)
    )


# ---------------------------------------------------------------------------
# A "no" in a payload is not a "yes"
# ---------------------------------------------------------------------------
#
# The flag check was plain truthiness, so any non-empty string counted: a POS or a
# spreadsheet column reading `priority: "normal"` made the order T1 - priced and held
# as urgent - and `next_day: "N"` made it T3.


@pytest.mark.parametrize(
    "value",
    [
        "normal", "N", "no", "No", "false", "0", "", "  ", "standard", "off", 0, False, None,
        "routine", "Same Day", "same-day", "today", "tomorrow", "next_day", "Overnight",
        "N/A", "-", "low", "Medium",
    ],
)
def test_a_negative_priority_is_not_a_rush(value):
    tier, _ = classify_tier(make_order(raw_payload={"priority": value}))
    assert tier == "T2"


@pytest.mark.parametrize("value", ["N", "no", "false", "0"])
def test_a_negative_next_day_is_not_scheduled(value):
    tier, _ = classify_tier(make_order(raw_payload={"next_day": value}))
    assert tier == "T2"


@pytest.mark.parametrize("value", ["N", "no", "0", False])
def test_a_negative_hot_shot_is_not_a_hot_shot(value):
    tier, _ = classify_tier(make_order(raw_payload={"hot_shot": value}))
    assert tier == "T2"


@pytest.mark.parametrize("value", [True, 1, "Y", "yes", "TRUE", "1", "high", "rush", "x"])
def test_a_set_flag_still_counts(value):
    """Only negatives were added. Anything else that was a "yes" still is."""
    tier, _ = classify_tier(make_order(raw_payload={"priority": value}))
    assert tier == "T1"


def test_a_word_means_the_same_in_a_payload_as_in_a_manifest():
    """The manifest upload reads a priority column by its own vocabulary. A word it
    calls not urgent must not force T1 when the same word arrives in a POS payload's
    `priority` field, and a word it calls urgent must still count as set."""
    from app.ingestion.manifest import _DEADLINE_WORDS

    for word, choice in _DEADLINE_WORDS.items():
        tier, _ = classify_tier(make_order(raw_payload={"priority": word}))
        if choice in ("today", "tomorrow"):
            assert tier == "T2", word
        else:
            assert tier == "T1", word
