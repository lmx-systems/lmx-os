"""The hub's own day and month - the units costing and settlement cut time by."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.hub_calendar import hub_days, hub_month
from app.models.hub import Hub

LOS_ANGELES = Hub(name="Hub", lat=34.05, lng=-118.24, timezone="America/Los_Angeles")


def _day_of(at: datetime) -> tuple[datetime, datetime]:
    (day,) = hub_days(LOS_ANGELES, at, at + timedelta(minutes=1))
    return day


def test_a_day_runs_midnight_to_midnight_on_the_hub_clock():
    midnight = datetime(2026, 9, 17, 7, 0, tzinfo=timezone.utc)  # 00:00 PDT
    assert _day_of(midnight + timedelta(hours=9)) == (midnight, midnight + timedelta(days=1))


def test_an_evening_past_utc_midnight_is_the_same_day():
    """UTC midnight is 5pm here in summer. A day cut there splits every evening."""
    four_pm = datetime(2026, 9, 17, 23, 0, tzinfo=timezone.utc)
    assert _day_of(four_pm) == _day_of(four_pm + timedelta(hours=2))


def test_the_day_the_clocks_go_back_is_twenty_five_hours():
    """Measured by subtraction, the way payroll measures a span. Bounds sharing
    a local tzinfo would subtract by wall clock and come out at 24."""
    start, end = _day_of(datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc))
    assert end - start == timedelta(hours=25)


def test_the_day_the_clocks_go_forward_is_twenty_three_hours():
    start, end = _day_of(datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc))
    assert end - start == timedelta(hours=23)


def test_it_returns_every_overlapping_day_and_no_other():
    since = datetime(2026, 9, 17, 0, 0, tzinfo=timezone.utc)  # 5pm PDT on the 16th
    days = hub_days(LOS_ANGELES, since, since + timedelta(days=1))
    local = ZoneInfo("America/Los_Angeles")
    assert [start.astimezone(local).date().isoformat() for start, _ in days] == [
        "2026-09-16", "2026-09-17",
    ]


def test_a_month_begins_and_ends_at_the_hubs_midnight():
    assert hub_month(LOS_ANGELES, 2026, 8) == (
        datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc),
    )


def test_december_ends_in_january_on_winter_time():
    _, end = hub_month(LOS_ANGELES, 2026, 12)
    assert end == datetime(2027, 1, 1, 8, 0, tzinfo=timezone.utc)
