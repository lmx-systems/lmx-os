"""
app/payroll/overtime_rules.py - the pluggable per-state overtime rule
registry (docs/ROADMAP.md A9). No real state-specific rule is registered
yet (that's a business/legal decision, see
docs/PAYROLL_STATE_OT_RESEARCH.md) - these tests confirm the mechanism
itself: unset/unknown state codes fall back to the federal rule, and a
registered rule is actually picked up, without asserting any real state's
policy.
"""
from datetime import date
from unittest.mock import patch

from app.payroll.overtime_rules import (
    FEDERAL_OVERTIME_THRESHOLD_HOURS,
    FederalWeeklyOvertimeRule,
    OvertimeRule,
    overtime_rule_for_state,
)


def test_no_state_code_falls_back_to_federal():
    assert isinstance(overtime_rule_for_state(None), FederalWeeklyOvertimeRule)


def test_unregistered_state_code_falls_back_to_federal():
    assert isinstance(overtime_rule_for_state("ZZ"), FederalWeeklyOvertimeRule)


def test_state_code_lookup_is_case_insensitive():
    class FakeRule(OvertimeRule):
        def apply(self, daily_hours):
            return 0.0, 0.0

    fake = FakeRule()
    with patch("app.payroll.overtime_rules.STATE_OVERTIME_RULES", {"CA": fake}):
        assert overtime_rule_for_state("ca") is fake
        assert overtime_rule_for_state("CA") is fake


def test_federal_rule_pays_no_overtime_at_or_under_the_threshold():
    rule = FederalWeeklyOvertimeRule()
    daily_hours = {date(2026, 6, 1): 8.0, date(2026, 6, 2): 8.0}
    regular, overtime = rule.apply(daily_hours)
    assert regular == 16.0
    assert overtime == 0.0


def test_federal_rule_applies_only_a_weekly_threshold_not_a_daily_one():
    # 12 hours in one day, nothing else - well over any daily threshold a
    # state rule might apply, but the federal-only rule doesn't look at
    # daily totals at all, only the weekly sum.
    rule = FederalWeeklyOvertimeRule()
    daily_hours = {date(2026, 6, 1): 12.0}
    regular, overtime = rule.apply(daily_hours)
    assert regular == 12.0
    assert overtime == 0.0


def test_federal_rule_splits_regular_and_overtime_at_the_weekly_threshold():
    rule = FederalWeeklyOvertimeRule()
    daily_hours = {date(2026, 6, i): 9.0 for i in range(1, 6)}  # 45 total
    regular, overtime = rule.apply(daily_hours)
    assert regular == FEDERAL_OVERTIME_THRESHOLD_HOURS
    assert overtime == 5.0
