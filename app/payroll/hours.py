"""
Real hours-worked/overtime calculation shared by the driver-facing
GET /driver/me/earnings (app/api/driver_routes.py, "earnings so far this
period") and the admin-triggered payroll run (app/api/admin_routes.py,
"submit the last *completed* period's hours"). One place, so the two
never drift on what "hours worked" means.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver_shift_event import DriverShiftEvent

# Fallback only - used when a driver has no real hourly_rate_cents set yet
# (app/models/driver.py). Not tuned against any real wage decision - see
# docs/NEXT_STEPS.md.
PLACEHOLDER_HOURLY_RATE_CENTS = 1_800  # $18.00/hr

# Federal FLSA overtime only (1.5x past 40hrs in a workweek) - applied to
# w2 drivers only, since 1099 contractors aren't FLSA overtime-eligible
# and gig per-delivery pay has no hours-based overtime concept. No
# state-specific daily-OT rules (e.g. California's 8hr/day threshold) are
# modeled - a real policy gap, not silently assumed away.
FEDERAL_OVERTIME_THRESHOLD_HOURS = 40.0
FEDERAL_OVERTIME_MULTIPLIER = 1.5

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


async def hours_worked_from_shift_events(
    session: AsyncSession, driver_id: str, start: datetime, end: datetime
) -> float:
    """
    Real on-duty time from the shift-event log (app/models/
    driver_shift_event.py), replacing the old route-span heuristic. Only
    update_my_availability ever crosses the on-duty/off-duty boundary (see
    ON_DUTY_STATUSES above), so that endpoint's log is complete for this
    purpose even though accept_offer/decline_offer also change status
    (they only move within the already-on-duty sub-states).
    """
    result = await session.execute(
        select(DriverShiftEvent)
        .where(DriverShiftEvent.driver_id == uuid.UUID(driver_id), DriverShiftEvent.occurred_at < end)
        .order_by(DriverShiftEvent.occurred_at)
    )
    events = list(result.scalars().all())
    if not events:
        return 0.0

    # Was the driver already on duty when the window opened? - the most
    # recent event strictly before `start` tells us.
    on_duty_since: datetime | None = None
    prior = [e for e in events if e.occurred_at < start]
    if prior and prior[-1].event_type in ON_DUTY_STATUSES:
        on_duty_since = start

    total_seconds = 0.0
    for event in events:
        if event.occurred_at < start:
            continue
        if event.event_type in ON_DUTY_STATUSES:
            if on_duty_since is None:
                on_duty_since = event.occurred_at
        elif on_duty_since is not None:
            total_seconds += (event.occurred_at - on_duty_since).total_seconds()
            on_duty_since = None

    if on_duty_since is not None:
        total_seconds += (end - on_duty_since).total_seconds()

    return max(total_seconds / 3600, 0.0)


async def hours_and_overtime(
    session: AsyncSession, driver_id: str, start: datetime, end: datetime
) -> tuple[float, float]:
    """Regular + overtime hours within [start, end), bucketed into
    Monday-Sunday federal workweeks. Known limitation: a workweek that
    straddles two pay periods is only evaluated using the hours visible
    within THIS period - hours from the adjacent period aren't looked up,
    so OT could be undercounted right at a pay-period boundary. A real
    payroll system (app/payroll/, once actually wired to Rippling) should
    be the system of record for cross-period OT, not this estimate."""
    total_regular = 0.0
    total_overtime = 0.0
    for week_start, week_end in _calendar_weeks_overlapping(start, end):
        clipped_start, clipped_end = max(week_start, start), min(week_end, end)
        week_hours = await hours_worked_from_shift_events(session, driver_id, clipped_start, clipped_end)
        if week_hours > FEDERAL_OVERTIME_THRESHOLD_HOURS:
            total_regular += FEDERAL_OVERTIME_THRESHOLD_HOURS
            total_overtime += week_hours - FEDERAL_OVERTIME_THRESHOLD_HOURS
        else:
            total_regular += week_hours
    return total_regular, total_overtime


async def hours_and_pay_for_period(
    session: AsyncSession, *, driver_id: str, employment_type: str, rate_cents: int, start: datetime, end: datetime
) -> tuple[float, float, int]:
    """Returns (regular_hours, overtime_hours, estimated_pay_cents) for
    [start, end) - overtime only applies to w2 (see hours_and_overtime's
    docstring on why 1099/gig don't get it)."""
    if employment_type == "w2":
        regular_hours, overtime_hours = await hours_and_overtime(session, driver_id, start, end)
    else:
        regular_hours = await hours_worked_from_shift_events(session, driver_id, start, end)
        overtime_hours = 0.0

    regular_pay_cents = round(regular_hours * rate_cents)
    overtime_pay_cents = round(overtime_hours * rate_cents * FEDERAL_OVERTIME_MULTIPLIER)
    return regular_hours, overtime_hours, regular_pay_cents + overtime_pay_cents
