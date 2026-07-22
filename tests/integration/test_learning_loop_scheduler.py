"""
app/learning_loop/scheduler.py (docs/ROADMAP.md E7) against real Postgres +
Redis. The real wall clock is replaced with a fixed instant so "is it 2am
in this hub's timezone yet" is deterministic - see _FixedDatetime.
"""
import uuid
from datetime import datetime, timezone

import pytest

import app.learning_loop.scheduler as scheduler_module
from app.learning_loop.scheduler import LearningLoopScheduler, _last_run_date_key, _lock_key
from app.models.hub import Hub

pytestmark = pytest.mark.integration


class _FixedDatetime(datetime):
    """Stands in for the module's `datetime` name so `.now(tz)` returns a
    chosen instant instead of the real wall clock."""

    _instant = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        instant = cls._instant
        return instant.astimezone(tz) if tz else instant


def _set_fixed_utc_hour(monkeypatch, hour: int) -> None:
    fixed = _FixedDatetime
    fixed._instant = datetime(2026, 7, 22, hour, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler_module, "datetime", fixed)


async def _seed_hub(db_session, *, tz: str = "UTC") -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Scheduler Test Hub", timezone=tz, lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.commit()
    return hub


async def test_runs_at_the_hubs_local_nightly_hour(db_session, real_redis_client, monkeypatch):
    hub = await _seed_hub(db_session, tz="UTC")
    _set_fixed_utc_hour(monkeypatch, scheduler_module.NIGHTLY_RUN_LOCAL_HOUR)

    scheduler = LearningLoopScheduler()
    await scheduler.maybe_run_for_hub(hub)

    last_run = await real_redis_client.get(_last_run_date_key(str(hub.id)))
    assert last_run == "2026-07-22"


async def test_does_not_run_outside_the_nightly_hour(db_session, real_redis_client, monkeypatch):
    hub = await _seed_hub(db_session, tz="UTC")
    _set_fixed_utc_hour(monkeypatch, scheduler_module.NIGHTLY_RUN_LOCAL_HOUR + 5)

    scheduler = LearningLoopScheduler()
    await scheduler.maybe_run_for_hub(hub)

    last_run = await real_redis_client.get(_last_run_date_key(str(hub.id)))
    assert last_run is None


async def test_does_not_run_twice_in_the_same_local_day(db_session, real_redis_client, monkeypatch):
    hub = await _seed_hub(db_session, tz="UTC")
    _set_fixed_utc_hour(monkeypatch, scheduler_module.NIGHTLY_RUN_LOCAL_HOUR)

    scheduler = LearningLoopScheduler()
    await scheduler.maybe_run_for_hub(hub)
    first_run_date = await real_redis_client.get(_last_run_date_key(str(hub.id)))

    # Second poll tick, same day, same hour window - must be a no-op:
    # last_run_date already matches today, so this call returns before
    # ever touching the lock or re-running the job.
    await scheduler.maybe_run_for_hub(hub)
    second_run_date = await real_redis_client.get(_last_run_date_key(str(hub.id)))

    assert first_run_date == second_run_date == "2026-07-22"


async def test_respects_an_already_held_lock_from_another_instance(db_session, real_redis_client, monkeypatch):
    hub = await _seed_hub(db_session, tz="UTC")
    await real_redis_client.set(_lock_key(str(hub.id)), "some-other-instance", nx=True, ex=600)
    _set_fixed_utc_hour(monkeypatch, scheduler_module.NIGHTLY_RUN_LOCAL_HOUR)

    scheduler = LearningLoopScheduler()
    await scheduler.maybe_run_for_hub(hub)

    # Never ran - the lock was already held.
    assert await real_redis_client.get(_last_run_date_key(str(hub.id))) is None
    # The other instance's lock value is untouched - this instance never deleted it.
    assert await real_redis_client.get(_lock_key(str(hub.id))) == "some-other-instance"


async def test_handles_an_unknown_timezone_without_crashing(db_session, real_redis_client):
    hub = await _seed_hub(db_session, tz="Not/A_Real_Zone")

    scheduler = LearningLoopScheduler()
    await scheduler.maybe_run_for_hub(hub)  # must not raise

    assert await real_redis_client.get(_last_run_date_key(str(hub.id))) is None


async def test_different_hub_timezones_trigger_at_different_utc_instants(db_session, real_redis_client, monkeypatch):
    """A hub 8 hours behind UTC hits its own 2am well after a UTC hub does -
    this is the whole reason the scheduler reads Hub.timezone per hub
    instead of using one fixed UTC hour for everyone."""
    utc_hub = await _seed_hub(db_session, tz="UTC")
    la_hub = await _seed_hub(db_session, tz="America/Los_Angeles")  # UTC-7 in July (DST)

    # 02:00 UTC - it's 2am for the UTC hub, but only 7pm the previous day
    # for the LA hub in July (UTC-7) - not LA's 2am yet.
    _set_fixed_utc_hour(monkeypatch, 2)
    scheduler = LearningLoopScheduler()
    await scheduler.maybe_run_for_hub(utc_hub)
    await scheduler.maybe_run_for_hub(la_hub)

    assert await real_redis_client.get(_last_run_date_key(str(utc_hub.id))) is not None
    assert await real_redis_client.get(_last_run_date_key(str(la_hub.id))) is None
