"""
Real hours-worked/overtime calculation shared by the driver-facing
GET /driver/me/earnings (app/api/driver_routes.py, "earnings so far this
period") and the admin-triggered payroll run (app/api/admin_routes.py,
"submit the last *completed* period's hours"). One place, so the two
never drift on what "hours worked" means.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver_shift_event import DriverShiftEvent
from app.models.gig_payout import GigPayout
from app.models.hub import Hub
from app.payroll.overtime_rules import FEDERAL_OVERTIME_MULTIPLIER, overtime_rule_for_state

# Fallback only - used when a driver has no real hourly_rate_cents set yet
# (app/models/driver.py). Not tuned against any real wage decision - see
# docs/NEXT_STEPS.md.
PLACEHOLDER_HOURLY_RATE_CENTS = 1_800  # $18.00/hr

# Which overtime *multiplier* applies to a driver's overtime hours - unlike
# the *threshold* (now pluggable per state, app/payroll/overtime_rules.py),
# every rule researched so far still pays 1.5x, not a distinct double-time
# tier (see docs/PAYROLL_STATE_OT_RESEARCH.md's note on California's 2x
# past 12hrs/day - not modeled here, since no state rule is registered
# yet). Applied to w2 drivers only, since 1099 contractors aren't FLSA
# overtime-eligible and gig per-delivery pay has no hours-based overtime
# concept.

# On-duty (available/en_route/offered are all "online, on the clock"
# sub-states an operator never toggles directly - only
# update_my_availability's off_shift/on_break crossing is ever logged as a
# DriverShiftEvent, see that endpoint) vs. not (off_shift/on_break).
ON_DUTY_STATUSES = {"available", "en_route", "offered"}


def week_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_start = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, next_start


def pay_period_bounds(employment_type: str, now: datetime) -> tuple[datetime, datetime]:
    """The *current*, still-accumulating pay period as of `now`. w2 drivers
    are paid monthly, 1099 contractors weekly (Sourabh's stated cadence).
    gig falls back to weekly too, pending Phase 3's real per-delivery
    pricing model - there's no gig-specific earnings source to compute
    from yet."""
    if employment_type == "w2":
        return month_bounds(now)
    return week_bounds(now)


def previous_pay_period_bounds(employment_type: str, now: datetime) -> tuple[datetime, datetime]:
    """The most recently *completed* pay period as of `now` - a payroll run
    pays for a period that has already ended, not the one still in
    progress (pay_period_bounds above)."""
    current_start, _ = pay_period_bounds(employment_type, now)
    return pay_period_bounds(employment_type, current_start - timedelta(days=1))


def _calendar_weeks_overlapping(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Monday-aligned calendar workweeks overlapping [start, end) - federal
    OT is computed per fixed 7-day workweek, not per pay period."""
    cursor = (start - timedelta(days=start.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    weeks = []
    while cursor < end:
        weeks.append((cursor, cursor + timedelta(days=7)))
        cursor += timedelta(days=7)
    return weeks


async def _on_duty_spans(
    session: AsyncSession, driver_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """The real [on-duty-start, on-duty-end) spans within [start, end),
    from the shift-event log (app/models/driver_shift_event.py), replacing
    the old route-span heuristic. Only update_my_availability ever crosses
    the on-duty/off-duty boundary (see ON_DUTY_STATUSES above), so that
    endpoint's log is complete for this purpose even though accept_offer/
    decline_offer also change status (they only move within the
    already-on-duty sub-states). Shared by hours_worked_from_shift_events
    (sums span durations) and daily_hours_worked_from_shift_events (splits
    them across day boundaries) so the two can never drift on what counts
    as "on duty"."""
    result = await session.execute(
        select(DriverShiftEvent)
        .where(DriverShiftEvent.driver_id == uuid.UUID(driver_id), DriverShiftEvent.occurred_at < end)
        .order_by(DriverShiftEvent.occurred_at)
    )
    events = list(result.scalars().all())
    if not events:
        return []

    # Was the driver already on duty when the window opened? - the most
    # recent event strictly before `start` tells us.
    on_duty_since: datetime | None = None
    prior = [e for e in events if e.occurred_at < start]
    if prior and prior[-1].event_type in ON_DUTY_STATUSES:
        on_duty_since = start

    spans: list[tuple[datetime, datetime]] = []
    for event in events:
        if event.occurred_at < start:
            continue
        if event.event_type in ON_DUTY_STATUSES:
            if on_duty_since is None:
                on_duty_since = event.occurred_at
        elif on_duty_since is not None:
            spans.append((on_duty_since, event.occurred_at))
            on_duty_since = None

    if on_duty_since is not None:
        spans.append((on_duty_since, end))

    return spans


async def hours_worked_from_shift_events(
    session: AsyncSession, driver_id: str, start: datetime, end: datetime
) -> float:
    spans = await _on_duty_spans(session, driver_id, start, end)
    total_seconds = sum((span_end - span_start).total_seconds() for span_start, span_end in spans)
    return max(total_seconds / 3600, 0.0)


async def daily_hours_worked_from_shift_events(
    session: AsyncSession, driver_id: str, start: datetime, end: datetime
) -> dict[date, float]:
    """Same on-duty reconstruction as hours_worked_from_shift_events, but
    bucketed per calendar day instead of summed - the mechanism a
    daily-threshold overtime rule (app/payroll/overtime_rules.py, e.g. a
    future California-style 8hr/day rule) needs and the federal-only rule
    doesn't. Built now precisely so adding such a rule later is "write an
    OvertimeRule.apply()", not "first go build a way to know daily
    hours" - nothing in this codebase computed per-day totals before this
    existed. A span that crosses midnight is split at the boundary, so
    e.g. a 10pm-2am span attributes 2 hours to the first day and 2 to the
    next, not 4 to either."""
    spans = await _on_duty_spans(session, driver_id, start, end)
    daily: dict[date, float] = {}
    for span_start, span_end in spans:
        cursor = span_start
        while cursor < span_end:
            next_midnight = datetime.combine(cursor.date() + timedelta(days=1), time.min, tzinfo=cursor.tzinfo)
            day_end = min(next_midnight, span_end)
            daily[cursor.date()] = daily.get(cursor.date(), 0.0) + (day_end - cursor).total_seconds() / 3600
            cursor = day_end
    return daily


async def hours_and_overtime(
    session: AsyncSession, driver_id: str, hub_id: str, start: datetime, end: datetime
) -> tuple[float, float]:
    """Regular + overtime hours within [start, end), bucketed into
    Monday-Sunday workweeks and evaluated against whichever OvertimeRule
    applies to the driver's hub (app/payroll/overtime_rules.py) - the
    federal-only rule for every hub today, since no state-specific rule is
    registered yet (see docs/PAYROLL_STATE_OT_RESEARCH.md). Known
    limitation: a workweek that straddles two pay periods is only
    evaluated using the hours visible within THIS period - hours from the
    adjacent period aren't looked up, so OT could be undercounted right at
    a pay-period boundary. A real payroll system (app/payroll/, once
    actually wired to Rippling) should be the system of record for
    cross-period OT, not this estimate."""
    hub = await session.get(Hub, uuid.UUID(hub_id))
    rule = overtime_rule_for_state(hub.state_code if hub else None)

    total_regular = 0.0
    total_overtime = 0.0
    for week_start, week_end in _calendar_weeks_overlapping(start, end):
        clipped_start, clipped_end = max(week_start, start), min(week_end, end)
        daily_hours = await daily_hours_worked_from_shift_events(session, driver_id, clipped_start, clipped_end)
        week_regular, week_overtime = rule.apply(daily_hours)
        total_regular += week_regular
        total_overtime += week_overtime
    return total_regular, total_overtime


async def gig_payout_total_cents_for_period(
    session: AsyncSession, driver_id: str, start: datetime, end: datetime
) -> int:
    """Real per-delivery earnings for a gig-classified driver
    (docs/ROADMAP.md A11) - summed from GigPayout rows
    (app/api/driver_routes.py's complete_stop, app/payroll/gig_pricing.py),
    not hours x rate like w2/1099. GigPayout.created_at is a reasonable
    period-bucketing proxy: a row is created at the exact moment its
    delivery was completed and paid, not backdated."""
    result = await session.execute(
        select(func.coalesce(func.sum(GigPayout.amount_cents), 0)).where(
            GigPayout.driver_id == uuid.UUID(driver_id),
            GigPayout.created_at >= start,
            GigPayout.created_at < end,
        )
    )
    return int(result.scalar_one())


async def hours_and_pay_for_period(
    session: AsyncSession,
    *,
    driver_id: str,
    hub_id: str,
    employment_type: str,
    rate_cents: int,
    start: datetime,
    end: datetime,
) -> tuple[float, float, int]:
    """Returns (regular_hours, overtime_hours, estimated_pay_cents) for
    [start, end) - overtime only applies to w2 (see hours_and_overtime's
    docstring on why 1099/gig don't get it). `hours_worked` is still
    reported for gig (a driver's real on-duty time is informational
    either way), but `estimated_pay_cents` comes from real per-delivery
    payouts instead of hours x rate - unlike 1099, gig is genuinely not
    paid by the hour at all (docs/ROADMAP.md A11)."""
    if employment_type == "w2":
        regular_hours, overtime_hours = await hours_and_overtime(session, driver_id, hub_id, start, end)
        regular_pay_cents = round(regular_hours * rate_cents)
        overtime_pay_cents = round(overtime_hours * rate_cents * FEDERAL_OVERTIME_MULTIPLIER)
        return regular_hours, overtime_hours, regular_pay_cents + overtime_pay_cents

    regular_hours = await hours_worked_from_shift_events(session, driver_id, start, end)
    overtime_hours = 0.0

    if employment_type == "gig":
        pay_cents = await gig_payout_total_cents_for_period(session, driver_id, start, end)
        return regular_hours, overtime_hours, pay_cents

    regular_pay_cents = round(regular_hours * rate_cents)
    return regular_hours, overtime_hours, regular_pay_cents
