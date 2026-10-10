"""
app/payroll/hours.py - real hours-worked/overtime math shared by
GET /driver/me/earnings and the admin payroll-run endpoint. Uses fixed,
explicit dates throughout rather than datetime.now(), since the overtime
tests need shift events to land within a specific Monday-Sunday workweek
regardless of what day the test suite happens to run on.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import app.payroll.hours as payroll_hours
from app.models.driver import Driver
from app.models.driver_shift_event import DriverShiftEvent
from app.models.hub import Hub
from app.payroll.overtime_rules import OvertimeRule

pytestmark = pytest.mark.integration

# The test hub keeps Los Angeles time, and payroll's weeks are the hub's.
LA = ZoneInfo("America/Los_Angeles")
# A fixed Monday, arbitrary but deterministic - midnight on the hub's clock.
MONDAY = datetime(2026, 6, 1, tzinfo=LA)


async def _seed_driver(db_session):
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        Hub(id=hub_id, name="Payroll Hours Test Hub", lat=34.05, lng=-118.25,
            timezone="America/Los_Angeles")
    )
    await db_session.commit()
    db_session.add(Driver(id=driver_id, hub_id=hub_id, name="Pat H.", phone="+15555550301", vehicle_capacity_units=5))
    await db_session.commit()
    return hub_id, driver_id


def test_month_bounds_handles_december_year_rollover():
    start, end = payroll_hours.month_bounds(datetime(2026, 12, 15, tzinfo=timezone.utc), LA)
    assert start == datetime(2026, 12, 1, tzinfo=LA)
    assert end == datetime(2027, 1, 1, tzinfo=LA)


def test_pay_period_bounds_is_monthly_for_w2_and_weekly_for_others():
    now = datetime(2026, 6, 17, 12, tzinfo=timezone.utc)
    w2_start, w2_end = payroll_hours.pay_period_bounds("w2", now, LA)
    assert (w2_start, w2_end) == payroll_hours.month_bounds(now, LA)

    contractor_start, contractor_end = payroll_hours.pay_period_bounds("contractor_1099", now, LA)
    assert (contractor_start, contractor_end) == payroll_hours.week_bounds(now, LA)

    gig_start, gig_end = payroll_hours.pay_period_bounds("gig", now, LA)
    assert (gig_start, gig_end) == payroll_hours.week_bounds(now, LA)


def test_previous_pay_period_bounds_steps_back_one_full_period():
    now = datetime(2026, 6, 17, tzinfo=timezone.utc)  # mid-June
    prev_start, prev_end = payroll_hours.previous_pay_period_bounds("w2", now, LA)
    assert prev_start == datetime(2026, 5, 1, tzinfo=LA)
    assert prev_end == datetime(2026, 6, 1, tzinfo=LA)


def test_a_week_starts_at_the_hubs_monday_midnight():
    """1am UTC on a Monday is 6pm the Sunday before in Los Angeles - still the
    previous week on the hub's clock, which is the one the driver worked."""
    start, end = payroll_hours.week_bounds(datetime(2026, 6, 1, 1, tzinfo=timezone.utc), LA)
    assert start == datetime(2026, 5, 25, tzinfo=LA)
    assert end == datetime(2026, 6, 1, tzinfo=LA)


def test_the_week_the_clocks_go_forward_is_167_hours():
    """Monday midnight to Monday midnight on the hub's clock, across the spring
    change. Stepping back a fixed day from the current week's start would land
    on the Saturday; a minute back lands in the right week either way."""
    start, end = payroll_hours.previous_pay_period_bounds(
        "contractor_1099", datetime(2026, 3, 10, 12, tzinfo=timezone.utc), LA
    )
    assert start == datetime(2026, 3, 2, tzinfo=LA)
    assert end - start == timedelta(hours=167)


async def test_hours_worked_pairs_an_online_to_offline_span(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=9)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=MONDAY + timedelta(hours=13)),
        ]
    )
    await db_session.commit()

    hours = await payroll_hours.hours_worked_from_shift_events(
        db_session, str(driver_id), MONDAY, MONDAY + timedelta(days=7)
    )
    assert hours == pytest.approx(4.0)


async def test_hours_worked_clips_an_open_ended_span_to_the_window_end(db_session):
    """No closing off_shift event within the window - the driver is still
    on duty as of `end`, so hours count up to `end`, not indefinitely."""
    hub_id, driver_id = await _seed_driver(db_session)
    db_session.add(
        DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=9))
    )
    await db_session.commit()

    hours = await payroll_hours.hours_worked_from_shift_events(
        db_session, str(driver_id), MONDAY, MONDAY + timedelta(hours=15)
    )
    assert hours == pytest.approx(6.0)


async def test_hours_worked_counts_a_span_that_started_before_the_window(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY - timedelta(hours=2)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=MONDAY + timedelta(hours=1)),
        ]
    )
    await db_session.commit()

    # Window opens at MONDAY - the 2 hours before it don't count, only the
    # 1 hour actually inside [MONDAY, ...).
    hours = await payroll_hours.hours_worked_from_shift_events(
        db_session, str(driver_id), MONDAY, MONDAY + timedelta(days=1)
    )
    assert hours == pytest.approx(1.0)


async def test_hours_worked_excludes_on_break_time(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=9)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="on_break", occurred_at=MONDAY + timedelta(hours=12)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=12, minutes=30)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=MONDAY + timedelta(hours=17)),
        ]
    )
    await db_session.commit()

    hours = await payroll_hours.hours_worked_from_shift_events(
        db_session, str(driver_id), MONDAY, MONDAY + timedelta(days=1)
    )
    # 8 hours on duty (9->17) minus a 30-minute break = 7.5.
    assert hours == pytest.approx(7.5)


async def test_daily_hours_splits_a_span_that_crosses_midnight(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=22)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=MONDAY + timedelta(hours=26)),
        ]
    )
    await db_session.commit()

    daily = await payroll_hours.daily_hours_worked_from_shift_events(
        db_session, str(driver_id), MONDAY, MONDAY + timedelta(days=7), LA
    )
    # 10pm Monday -> 2am Tuesday: 2 hours land on each side of midnight,
    # not 4 hours attributed to either day.
    assert daily[MONDAY.date()] == pytest.approx(2.0)
    assert daily[(MONDAY + timedelta(days=1)).date()] == pytest.approx(2.0)
    assert sum(daily.values()) == pytest.approx(4.0)


async def test_hours_and_overtime_uses_the_hub_state_rule_when_one_is_registered(db_session):
    """No real state rule exists yet (docs/PAYROLL_STATE_OT_RESEARCH.md) -
    this confirms hours_and_overtime actually looks one up via the hub's
    state_code and defers to it, using a fake rule rather than asserting
    any real state's policy."""
    hub_id, driver_id = await _seed_driver(db_session)
    hub = await db_session.get(Hub, hub_id)
    hub.state_code = "ZZ"
    await db_session.commit()

    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=1)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=MONDAY + timedelta(hours=9)),
        ]
    )
    await db_session.commit()

    class AllOvertimeRule(OvertimeRule):
        """A deliberately unrealistic fake rule - every on-duty hour is
        overtime - just to prove the registry lookup actually changes the
        result, not to model any real policy."""

        def apply(self, daily_hours):
            total = sum(daily_hours.values())
            return 0.0, total

    with patch("app.payroll.overtime_rules.STATE_OVERTIME_RULES", {"ZZ": AllOvertimeRule()}):
        regular, overtime = await payroll_hours.hours_and_overtime(
            db_session, str(driver_id), str(hub_id), MONDAY, MONDAY + timedelta(days=7)
        )
    assert regular == 0.0
    assert overtime == pytest.approx(8.0)


async def test_a_sunday_evening_shift_counts_toward_the_week_it_was_worked(db_session):
    """38 hours from Monday plus Sunday 4pm-8pm is 42 hours in one of the hub's
    workweeks: 40 regular and 2 overtime. Cut at UTC's Monday - 5pm on Sunday
    here - that week saw 39, the next saw 3, and no overtime was paid at all.

    Bounded in UTC, the way the payroll routes bound a period, because the old
    cut took its weeks from whatever timezone its bounds arrived in."""
    hub_id, driver_id = await _seed_driver(db_session)
    sunday_4pm = MONDAY + timedelta(days=6, hours=16)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=1)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=MONDAY + timedelta(hours=39)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=sunday_4pm),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=sunday_4pm + timedelta(hours=4)),
        ]
    )
    await db_session.commit()

    regular, overtime = await payroll_hours.hours_and_overtime(
        db_session, str(driver_id), str(hub_id),
        MONDAY.astimezone(timezone.utc), (MONDAY + timedelta(days=7)).astimezone(timezone.utc),
    )
    assert regular == pytest.approx(40.0)
    assert overtime == pytest.approx(2.0)


async def test_hours_and_overtime_applies_time_and_a_half_over_40_in_a_week(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    # 45 hours online, entirely within the Monday-Sunday week starting at
    # MONDAY - 5 hours over the federal 40hr/week threshold.
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=1)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=MONDAY + timedelta(hours=46)),
        ]
    )
    await db_session.commit()

    regular, overtime = await payroll_hours.hours_and_overtime(
        db_session, str(driver_id), str(hub_id), MONDAY, MONDAY + timedelta(days=7)
    )
    assert regular == pytest.approx(40.0)
    assert overtime == pytest.approx(5.0)


async def test_hours_and_overtime_stays_zero_under_the_weekly_threshold(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=1)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=MONDAY + timedelta(hours=9)),
        ]
    )
    await db_session.commit()

    regular, overtime = await payroll_hours.hours_and_overtime(
        db_session, str(driver_id), str(hub_id), MONDAY, MONDAY + timedelta(days=7)
    )
    assert regular == pytest.approx(8.0)
    assert overtime == 0.0


async def test_hours_and_pay_for_period_skips_overtime_for_non_w2(db_session):
    """1099/gig aren't FLSA overtime-eligible - even 45 hours in a week
    should pay flat, no 1.5x premium."""
    hub_id, driver_id = await _seed_driver(db_session)
    db_session.add_all(
        [
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="available", occurred_at=MONDAY + timedelta(hours=1)),
            DriverShiftEvent(driver_id=driver_id, hub_id=hub_id, event_type="off_shift", occurred_at=MONDAY + timedelta(hours=46)),
        ]
    )
    await db_session.commit()

    regular, overtime, pay_cents = await payroll_hours.hours_and_pay_for_period(
        db_session,
        driver_id=str(driver_id),
        hub_id=str(hub_id),
        employment_type="contractor_1099",
        rate_cents=2_000,
        start=MONDAY,
        end=MONDAY + timedelta(days=7),
    )
    assert overtime == 0.0
    assert regular == pytest.approx(45.0)
    assert pay_cents == round(45.0 * 2_000)
