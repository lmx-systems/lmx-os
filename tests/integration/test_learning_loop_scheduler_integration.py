"""
Learning Loop nightly scheduler (roadmap item E7) against real
Postgres + Redis: one tick runs due hubs exactly once (SET NX idempotency
marker), skips hubs outside their scheduled hour, and skips inactive hubs.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.learning_loop.scheduler import LearningLoopScheduler
from app.models.hub import Hub

pytestmark = pytest.mark.integration


def _fixed_now() -> datetime:
    # 10:30 UTC in January = 2:30am America/Los_Angeles (PST).
    return datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)


async def _seed_hub(db_session, *, tz: str, active: bool = True) -> uuid.UUID:
    hub_id = uuid.uuid4()
    db_session.add(
        Hub(id=hub_id, name=f"Sched Hub {hub_id.hex[:6]}", timezone=tz, lat=34.0, lng=-118.0, active=active)
    )
    await db_session.commit()
    return hub_id


async def test_tick_runs_due_hub_once_and_is_idempotent(db_session, real_redis_client):
    la_hub = await _seed_hub(db_session, tz="America/Los_Angeles")

    scheduler = LearningLoopScheduler()
    with patch("app.learning_loop.scheduler.settings") as mock_settings:
        mock_settings.learning_loop_schedule_hour = 2
        ran_first = await scheduler.run_due_hubs(now_utc=_fixed_now())
        ran_second = await scheduler.run_due_hubs(now_utc=_fixed_now())

    assert str(la_hub) in ran_first
    # Second tick within the same night: the SET NX marker already exists,
    # so the hub must not run again.
    assert str(la_hub) not in ran_second


async def test_tick_skips_hub_outside_its_scheduled_hour(db_session, real_redis_client):
    # 10:30 UTC is 5:30am in New York - not the 2am scheduled hour there.
    ny_hub = await _seed_hub(db_session, tz="America/New_York")

    scheduler = LearningLoopScheduler()
    with patch("app.learning_loop.scheduler.settings") as mock_settings:
        mock_settings.learning_loop_schedule_hour = 2
        ran = await scheduler.run_due_hubs(now_utc=_fixed_now())

    assert str(ny_hub) not in ran


async def test_tick_skips_inactive_hubs(db_session, real_redis_client):
    inactive_hub = await _seed_hub(db_session, tz="America/Los_Angeles", active=False)

    scheduler = LearningLoopScheduler()
    with patch("app.learning_loop.scheduler.settings") as mock_settings:
        mock_settings.learning_loop_schedule_hour = 2
        ran = await scheduler.run_due_hubs(now_utc=_fixed_now())

    assert str(inactive_hub) not in ran
