"""When a driver's last reported position is too old to dispatch to."""
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.optimizer.service import _position_is_stale

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)
LIMIT = timedelta(seconds=settings.driver_position_stale_after_seconds)


def test_a_recent_report_is_fresh():
    assert not _position_is_stale((NOW - timedelta(seconds=30)).isoformat(), NOW)


def test_a_report_older_than_the_limit_is_stale():
    assert _position_is_stale((NOW - LIMIT - timedelta(seconds=1)).isoformat(), NOW)


def test_a_report_without_a_zone_is_read_as_utc():
    naive = (NOW - timedelta(seconds=30)).replace(tzinfo=None).isoformat()
    assert not _position_is_stale(naive, NOW)


def test_an_unreadable_timestamp_counts_as_stale():
    assert _position_is_stale("not a time", NOW)
