"""
Learning Loop nightly scheduler (roadmap item E7) - the pure hub-local
"is it time yet" decision. The DB/Redis-touching run itself is covered in
tests/integration/test_learning_loop_scheduler_integration.py.
"""
from datetime import datetime, timezone

from app.learning_loop.scheduler import hub_is_due


def test_due_when_hub_local_hour_matches():
    # 10:30 UTC == 2:30am in Los Angeles (PDT, UTC-8... PDT is UTC-7; use a
    # date in January for PST/UTC-8 determinism).
    now = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
    due, local_date = hub_is_due(now, "America/Los_Angeles", schedule_hour=2)
    assert due is True
    assert local_date == "2026-01-15"


def test_not_due_outside_the_scheduled_hour():
    now = datetime(2026, 1, 15, 20, 30, tzinfo=timezone.utc)  # 12:30pm LA
    due, _ = hub_is_due(now, "America/Los_Angeles", schedule_hour=2)
    assert due is False


def test_two_hubs_in_different_timezones_are_due_at_different_utc_times():
    # 07:15 UTC = 2:15am New York (EST) but 11:15pm previous day LA.
    now = datetime(2026, 1, 15, 7, 15, tzinfo=timezone.utc)
    ny_due, _ = hub_is_due(now, "America/New_York", schedule_hour=2)
    la_due, _ = hub_is_due(now, "America/Los_Angeles", schedule_hour=2)
    assert ny_due is True
    assert la_due is False


def test_local_date_reflects_hub_timezone_not_utc():
    # 07:15 UTC on Jan 15 is still Jan 14 in LA - the idempotency marker
    # must use the hub-local date or a run near midnight UTC could claim
    # the wrong night.
    now = datetime(2026, 1, 15, 7, 15, tzinfo=timezone.utc)
    _, local_date = hub_is_due(now, "America/Los_Angeles", schedule_hour=23)
    assert local_date == "2026-01-14"


def test_bad_timezone_falls_back_to_utc_instead_of_crashing():
    now = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)
    due, local_date = hub_is_due(now, "Not/A_Real_Zone", schedule_hour=2)
    assert due is True
    assert local_date == "2026-01-15"
