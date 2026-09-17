"""The thing that makes DEC-0's log non-empty, and the guards around it.

Most of these assert that the scheduler *declines* to run. That is the bulk of
what it does: a cycle costs a solver call, and once DEC-3 points the optimizer at
a live Google project that call is billed. Every decline below is a call not
made, so each one is worth a test.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models.hub import Hub
from app.models.hub_closure import HubClosure
from app.models.shadow_decision import ShadowDecision
from app.redis_client import get_client
from app.shadow.scheduler import ShadowScheduler, _last_run_key, _lock_key
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture
async def hub(db_session):
    """A hub whose local time is UTC, so a test can reason about its hours."""
    hub = Hub(
        id=uuid.uuid4(),
        name="Shadow Hub",
        timezone="UTC",
        lat=30.27,
        lng=-97.74,
        active=True,
    )
    db_session.add(hub)
    await db_session.commit()
    yield hub


@pytest.fixture
def always_open(monkeypatch):
    """Operating hours that cover the clock, so a test never depends on when
    it runs."""
    monkeypatch.setattr(settings, "shadow_cycle_start_local_hour", 0)
    monkeypatch.setattr(settings, "shadow_cycle_end_local_hour", 24)


async def _cycles(db_session, hub) -> int:
    return len(
        (
            await db_session.scalars(
                select(ShadowDecision).where(ShadowDecision.hub_id == hub.id)
            )
        ).all()
    )


class TestItIsOffUntilSomebodyTurnsItOn:
    def test_the_default_is_off(self):
        """Not because shadow mode is unsafe - it writes no operational state -
        but because every cycle is a solver call, and against a live Google
        project that is billed. A loop making one every few minutes per hub
        would arrive as an invoice rather than as a decision."""
        assert settings.shadow_scheduler_enabled is False

    def test_start_does_nothing_while_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "shadow_scheduler_enabled", False)
        scheduler = ShadowScheduler()
        scheduler.start()
        assert scheduler._poll_task is None

    async def test_start_begins_the_loop_when_enabled(self, monkeypatch):
        """Async because `start` creates a task, and a task needs a loop - which
        is also why it is only ever called from the app's lifespan."""
        monkeypatch.setattr(settings, "shadow_scheduler_enabled", True)
        scheduler = ShadowScheduler()
        scheduler.start()
        assert scheduler._poll_task is not None
        await scheduler.stop()
        assert scheduler._poll_task is None

    async def test_stopping_one_that_never_started_is_safe(self):
        await ShadowScheduler().stop()


class TestWhenItDeclines:
    async def test_outside_operating_hours(self, db_session, hub, monkeypatch, real_redis_client):
        """A cycle at three in the morning plans an empty queue and records that
        it decided nothing - true, and it dilutes every rate in the report."""
        now = datetime.now(timezone.utc)
        monkeypatch.setattr(settings, "shadow_cycle_start_local_hour", (now.hour + 2) % 24)
        monkeypatch.setattr(settings, "shadow_cycle_end_local_hour", (now.hour + 3) % 24)
        scheduler = ShadowScheduler(cadence_seconds=1)

        assert await scheduler.maybe_run_for_hub(hub) is False
        assert await _cycles(db_session, hub) == 0

    async def test_within_the_cadence_of_the_last_recorded_cycle(
        self, hub, always_open, real_redis_client
    ):
        """Checked against what was last recorded, not against a timer - a
        process restart must not double the cadence."""
        await get_client().set(
            _last_run_key(str(hub.id)),
            (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
        )
        scheduler = ShadowScheduler(cadence_seconds=300)
        assert await scheduler.maybe_run_for_hub(hub) is False

    async def test_another_instance_holds_the_lock(self, hub, always_open, real_redis_client):
        await get_client().set(_lock_key(str(hub.id)), "1", ex=60)
        scheduler = ShadowScheduler(cadence_seconds=1)
        assert await scheduler.maybe_run_for_hub(hub) is False

    async def test_a_closed_day_is_skipped_and_not_retried(
        self, db_session, hub, always_open, real_redis_client
    ):
        """A hub that was not operating made no dispatcher decisions, so there
        is nothing for a shadow plan to have disagreed with. Marked as run so
        the poll loop does not retry it every thirty seconds all day."""
        db_session.add(
            HubClosure(hub_id=hub.id, closure_date=datetime.now(timezone.utc).date(),
                       reason="holiday")
        )
        await db_session.commit()
        scheduler = ShadowScheduler(cadence_seconds=1)

        assert await scheduler.maybe_run_for_hub(hub) is False
        assert await _cycles(db_session, hub) == 0
        assert await get_client().get(_last_run_key(str(hub.id))) is not None

    async def test_an_unreadable_timezone_declines_rather_than_crashes(
        self, db_session, always_open, real_redis_client
    ):
        bad = Hub(id=uuid.uuid4(), name="Bad TZ", timezone="Mars/Olympus",
                  lat=0.0, lng=0.0, active=True)
        db_session.add(bad)
        await db_session.commit()
        assert await ShadowScheduler(cadence_seconds=1).maybe_run_for_hub(bad) is False


class TestWhenItRuns:
    async def test_it_records_a_cycle(self, db_session, hub, always_open, real_redis_client):
        scheduler = ShadowScheduler(cadence_seconds=1)
        assert await scheduler.maybe_run_for_hub(hub) is True
        assert await _cycles(db_session, hub) == 1

    async def test_it_releases_the_lock_afterwards(
        self, hub, always_open, real_redis_client
    ):
        """A held lock wedges the hub out of every future cycle, so this is the
        failure that would look like the scheduler silently stopping."""
        scheduler = ShadowScheduler(cadence_seconds=1)
        await scheduler.maybe_run_for_hub(hub)
        assert await get_client().get(_lock_key(str(hub.id))) is None

    async def test_a_second_call_inside_the_cadence_does_not_run_again(
        self, db_session, hub, always_open, real_redis_client
    ):
        scheduler = ShadowScheduler(cadence_seconds=300)
        assert await scheduler.maybe_run_for_hub(hub) is True
        assert await scheduler.maybe_run_for_hub(hub) is False
        assert await _cycles(db_session, hub) == 1

    async def test_it_runs_again_once_the_cadence_has_passed(
        self, db_session, hub, always_open, real_redis_client
    ):
        scheduler = ShadowScheduler(cadence_seconds=300)
        assert await scheduler.maybe_run_for_hub(hub) is True
        await get_client().set(
            _last_run_key(str(hub.id)),
            (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat(),
        )
        assert await scheduler.maybe_run_for_hub(hub) is True
        assert await _cycles(db_session, hub) == 2

    async def test_a_corrupt_last_run_marker_does_not_wedge_the_hub(
        self, hub, always_open, real_redis_client
    ):
        await get_client().set(_last_run_key(str(hub.id)), "not a timestamp")
        assert await ShadowScheduler(cadence_seconds=300).maybe_run_for_hub(hub) is True

    async def test_a_failed_cycle_leaves_no_marker_so_the_next_poll_retries(
        self, hub, always_open, real_redis_client, monkeypatch
    ):
        """Waiting out a full cadence after a transient solver error would turn
        one failure into five minutes of missing evidence."""
        import app.shadow.scheduler as module

        async def boom(session, hub_id):
            raise RuntimeError("solver unavailable")

        monkeypatch.setattr(module, "record_shadow_cycle", boom)
        scheduler = ShadowScheduler(cadence_seconds=300)
        assert await scheduler.maybe_run_for_hub(hub) is False
        assert await get_client().get(_last_run_key(str(hub.id))) is None
        assert await get_client().get(_lock_key(str(hub.id))) is None


class TestTheCadenceIsReportedNotAssumed:
    async def test_the_report_measures_the_interval_it_actually_achieved(
        self, db_session, hub, always_open, real_redis_client
    ):
        """Config says 300s; a restart, a held lock or a closed hour produces
        gaps config does not know about. The row that bounds a claim has to be
        the real number."""
        from app.shadow.divergence import compute_divergence

        scheduler = ShadowScheduler(cadence_seconds=1)
        await scheduler.maybe_run_for_hub(hub)
        await get_client().delete(_last_run_key(str(hub.id)))
        await scheduler.maybe_run_for_hub(hub)
        await db_session.commit()

        now = datetime.now(timezone.utc)
        report = await compute_divergence(
            db_session,
            hub_id=hub.id,
            since=now - timedelta(days=1),
            until=now + timedelta(days=1),
        )
        interval = next(
            m for m in report.metrics if m.name == "interval between shadow cycles"
        )
        assert interval.sample_size == 1
        assert interval.median is not None
