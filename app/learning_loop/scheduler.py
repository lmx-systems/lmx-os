"""
Nightly scheduler for the Learning Loop's pattern-detection job (roadmap
item E7) - replaces "a person remembers to call the manual endpoint."

Design:
- A plain asyncio background task started from app.main's lifespan, not an
  external cron: no new infrastructure, survives exactly as long as the
  app does, and the manual endpoint (POST /learning-loop/{hub_id}/
  run-nightly-job) still exists unchanged for ops/testing.
- Each hub runs at learning_loop_schedule_hour *in that hub's own
  timezone* (Hub.timezone) - "nightly" means the hub's night, not the
  server's.
- A Redis SET NX marker (one per hub per local date) makes runs
  idempotent: restarting the app mid-night, or running more than one app
  instance (roadmap item E8), can't double-run a hub's job. Marker
  expires after 20h so the next night is always fresh.
- Failures are logged and skipped, never raised - one hub's bad night
  must not take the scheduler loop down with it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.learning_loop.service import run_nightly_job
from app.logging_config import get_logger
from app.models.hub import Hub
from app.redis_client import get_client

logger = get_logger(__name__)

CHECK_INTERVAL_SECONDS = 60
MARKER_TTL_SECONDS = 20 * 60 * 60  # < 24h so the next night's run is never blocked


def _marker_key(hub_id: str, local_date: str) -> str:
    return f"learning_loop:nightly_ran:{hub_id}:{local_date}"


def hub_is_due(now_utc: datetime, hub_timezone: str, schedule_hour: int) -> tuple[bool, str]:
    """Pure decision: is it currently `schedule_hour` in the hub's own
    timezone? Returns (due, hub-local date string for the idempotency
    marker). Split out from the loop for direct unit testing."""
    try:
        local_now = now_utc.astimezone(ZoneInfo(hub_timezone))
    except Exception:  # noqa: BLE001 - a bad tz string shouldn't kill the loop
        local_now = now_utc
    return local_now.hour == schedule_hour, local_now.date().isoformat()


class LearningLoopScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if not settings.learning_loop_scheduler_enabled:
            logger.info("learning_loop_scheduler_disabled")
            return
        self._task = asyncio.create_task(self._loop(), name="learning-loop-scheduler")
        logger.info(
            "learning_loop_scheduler_started",
            schedule_hour=settings.learning_loop_schedule_hour,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due_hubs()
            except Exception:  # noqa: BLE001 - keep the loop alive, see module docstring
                logger.warning("learning_loop_scheduler_tick_failed", exc_info=True)
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def run_due_hubs(self, now_utc: datetime | None = None) -> list[str]:
        """One scheduler tick: run the nightly job for every hub whose local
        time is in the scheduled hour and hasn't run tonight yet. Returns
        hub ids that actually ran (for tests/observability)."""
        now_utc = now_utc or datetime.now(timezone.utc)
        ran: list[str] = []

        async with session_scope() as session:
            result = await session.execute(select(Hub).where(Hub.active.is_(True)))
            hubs = list(result.scalars().all())

        for hub in hubs:
            due, local_date = hub_is_due(
                now_utc, hub.timezone, settings.learning_loop_schedule_hour
            )
            if not due:
                continue

            hub_id = str(hub.id)
            redis = get_client()
            # SET NX = "I claim tonight's run for this hub" - loses cleanly
            # if another instance (or an earlier tick) already claimed it.
            claimed = await redis.set(
                _marker_key(hub_id, local_date), "1", nx=True, ex=MARKER_TTL_SECONDS
            )
            if not claimed:
                continue

            try:
                async with session_scope() as session:
                    created = await run_nightly_job(session, hub_id=hub_id)
                logger.info(
                    "learning_loop_nightly_ran",
                    hub_id=hub_id,
                    proposals_created=len(created),
                )
                ran.append(hub_id)
            except Exception:  # noqa: BLE001
                logger.warning("learning_loop_nightly_failed", hub_id=hub_id, exc_info=True)

        return ran


learning_loop_scheduler = LearningLoopScheduler()
