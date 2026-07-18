from datetime import datetime, timezone

from app.learning_loop.detection import (
    HOLD_TOO_LONG_FLAG,
    HOLD_TOO_SHORT_FLAG,
    FlagRecord,
    detect_patterns,
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def make_flags(shop_id: str, flag_type: str, count: int) -> list[FlagRecord]:
    return [FlagRecord(shop_id=shop_id, flag_type=flag_type, created_at=NOW) for _ in range(count)]


def test_below_threshold_produces_no_pattern():
    flags = make_flags("shop-1", HOLD_TOO_SHORT_FLAG, 2)
    patterns = detect_patterns(flags, {"shop-1": {"T2": 45}}, min_occurrences=3)
    assert patterns == []


def test_hold_too_short_proposes_longer_window():
    flags = make_flags("shop-1", HOLD_TOO_SHORT_FLAG, 3)
    patterns = detect_patterns(
        flags, {"shop-1": {"T1": 10, "T2": 45, "T3": 120}}, min_occurrences=3, adjustment_minutes=10
    )
    assert len(patterns) == 1
    pattern = patterns[0]
    assert pattern.shop_id == "shop-1"
    assert pattern.proposed_tier_minutes["T2"] == 55
    assert pattern.proposed_tier_minutes["T1"] == 10  # untouched
    assert pattern.occurrence_count == 3


def test_hold_too_long_proposes_shorter_window():
    flags = make_flags("shop-1", HOLD_TOO_LONG_FLAG, 4)
    patterns = detect_patterns(
        flags, {"shop-1": {"T2": 45}}, min_occurrences=3, adjustment_minutes=10
    )
    assert patterns[0].proposed_tier_minutes["T2"] == 35


def test_proposed_window_never_goes_negative():
    flags = make_flags("shop-1", HOLD_TOO_LONG_FLAG, 5)
    patterns = detect_patterns(
        flags, {"shop-1": {"T2": 5}}, min_occurrences=3, adjustment_minutes=10
    )
    assert patterns[0].proposed_tier_minutes["T2"] == 0


def test_conflicting_signals_are_skipped():
    flags = make_flags("shop-1", HOLD_TOO_SHORT_FLAG, 3) + make_flags("shop-1", HOLD_TOO_LONG_FLAG, 3)
    patterns = detect_patterns(flags, {"shop-1": {"T2": 45}}, min_occurrences=3)
    assert patterns == []


def test_shop_with_no_baseline_minutes_is_skipped():
    flags = make_flags("shop-unknown", HOLD_TOO_SHORT_FLAG, 5)
    patterns = detect_patterns(flags, {}, min_occurrences=3)
    assert patterns == []


def test_confidence_increases_with_more_occurrences_but_stays_below_one():
    low = detect_patterns(
        make_flags("shop-1", HOLD_TOO_SHORT_FLAG, 3), {"shop-1": {"T2": 45}}, min_occurrences=3
    )[0]
    high = detect_patterns(
        make_flags("shop-1", HOLD_TOO_SHORT_FLAG, 20), {"shop-1": {"T2": 45}}, min_occurrences=3
    )[0]
    assert low.confidence < high.confidence
    assert high.confidence < 1.0


def test_multiple_shops_evaluated_independently():
    flags = make_flags("shop-1", HOLD_TOO_SHORT_FLAG, 3) + make_flags("shop-2", HOLD_TOO_LONG_FLAG, 3)
    patterns = detect_patterns(
        flags, {"shop-1": {"T2": 45}, "shop-2": {"T2": 45}}, min_occurrences=3
    )
    by_shop = {p.shop_id: p for p in patterns}
    assert by_shop["shop-1"].proposed_tier_minutes["T2"] == 55
    assert by_shop["shop-2"].proposed_tier_minutes["T2"] == 35


def test_irrelevant_flag_types_are_ignored():
    flags = [
        FlagRecord(shop_id="shop-1", flag_type="gate_code_needed", created_at=NOW) for _ in range(10)
    ]
    patterns = detect_patterns(flags, {"shop-1": {"T2": 45}}, min_occurrences=3)
    assert patterns == []
