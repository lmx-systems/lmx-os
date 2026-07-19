from datetime import datetime, timedelta, timezone

from app.schemas.order import NormalizedOrder
from app.sla.engine import (
    DEFAULT_HOLD_WINDOW_MINUTES,
    HoldWindowOverride,
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
