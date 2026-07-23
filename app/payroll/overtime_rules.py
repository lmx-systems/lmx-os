"""
Pluggable per-state overtime rule registry (docs/ROADMAP.md A9). Which
states need daily-OT rules beyond the federal 40hr/week baseline (e.g.
California's 8hr/day threshold), and what those rules should actually be,
is a business/legal decision - see docs/PAYROLL_STATE_OT_RESEARCH.md for
the research this needs before any state rule gets added here. This file
is the mechanism that decision plugs into, built in advance of the
decision itself: an OvertimeRule interface keyed by two-letter state code
(Hub.state_code), defaulting every driver to the existing federal-only
rule until a real one is registered. Adding a state's rule later is
"write an OvertimeRule subclass and register it in STATE_OVERTIME_RULES,"
not a change to app/payroll/hours.py's hour-reconstruction logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

FEDERAL_OVERTIME_THRESHOLD_HOURS = 40.0
FEDERAL_OVERTIME_MULTIPLIER = 1.5


class OvertimeRule(ABC):
    @abstractmethod
    def apply(self, daily_hours: dict[date, float]) -> tuple[float, float]:
        """(regular_hours, overtime_hours) for one Monday-Sunday calendar
        workweek, given each day's on-duty hours within it
        (app/payroll/hours.py's daily_hours_worked_from_shift_events). A
        rule with a daily threshold (unlike the federal-only one) gets
        real per-day totals to work with here, not just a weekly sum -
        that per-day reconstruction already exists precisely so a future
        rule doesn't have to rebuild it."""
        raise NotImplementedError


class FederalWeeklyOvertimeRule(OvertimeRule):
    """FLSA: 1.5x past 40 hours in the workweek, no daily threshold.
    Today's only rule, and every state's default until a real one is
    researched and registered below."""

    def apply(self, daily_hours: dict[date, float]) -> tuple[float, float]:
        week_hours = sum(daily_hours.values())
        if week_hours > FEDERAL_OVERTIME_THRESHOLD_HOURS:
            return FEDERAL_OVERTIME_THRESHOLD_HOURS, week_hours - FEDERAL_OVERTIME_THRESHOLD_HOURS
        return week_hours, 0.0


_FEDERAL_ONLY = FederalWeeklyOvertimeRule()

# Keyed by Hub.state_code (two-letter USPS code). Deliberately empty - no
# state-specific rule has been researched, legally reviewed, or approved
# yet (docs/PAYROLL_STATE_OT_RESEARCH.md). Add an entry here only once
# both (a) a real OvertimeRule subclass has been written against actual
# legal guidance for that state, and (b) the business decision to turn it
# on has actually been made - not preemptively, and not as a guess.
STATE_OVERTIME_RULES: dict[str, OvertimeRule] = {}


def overtime_rule_for_state(state_code: str | None) -> OvertimeRule:
    if state_code:
        rule = STATE_OVERTIME_RULES.get(state_code.upper())
        if rule is not None:
            return rule
    return _FEDERAL_ONLY
