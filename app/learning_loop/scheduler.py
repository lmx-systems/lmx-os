"""
Real scheduler for the Learning Loop's nightly pattern-detection job
(docs/ROADMAP.md E7) - replaces manual-trigger-only
(POST /learning-loop/{hub_id}/run-nightly-job) with a background loop that
actually runs it once a day, per hub, at that hub's own local nightly hour
(Hub.timezone) - not one fixed UTC hour, since a hub at 2am Eastern and one
at 2am Pacific are both "nightly" for their own operation, three hours
apart in real time.

Same "asyncio background task + Redis distributed lock, started once at
app startup" shape as app/events/bus.py's HubEventBus, just time-triggered
instead of event-triggered: no pub/sub, no external scheduler library
(APScheduler/Celery) - the hand-rolled-loop convention this codebase
already uses for the event bus is enough for "once a day," and a new
dependency for that would be a poor trade.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from app.db import session_scope
from app.learning_loop.service import run_nightly_job
from app.models.hub import Hub
from app.redis_client import get_client

logger = structlog.get_logger(__name__)

# Runs at 2am in each hub's own local time - the conventional "quiet hours"
# batch-job slot, not tied to any particular launch market's timezone.
NIGHTLY_RUN_LOCAL_HOUR = 2

# How often the loop wakes up to check whether any hub has crossed its own
# local 2am - frequent enough that the job never runs meaningfully late,
# infrequent enough to not matter at all as a cost.
DEFAULT_POLL_INTERVAL_SECONDS = 300.0  # 5 minutes

# Comfortably longer than one hub's nightly job should ever take - exists
# purely so a crashed/hung run doesn't permanently wedge that hub out of
# future runs; a live run releases the lock itself well before this.
LOCK_TTL_SECONDS = 600


def _last_run_date_key(hub_id: str) -> str:
    return f"learning_loop:last_run_date:{hub_id}"


def _lock_key(hub_id: str) -> str:
    return f"learning_loop:scheduler_running:{hub_id}"


class LearningLoopScheduler:
    def __init__(self, poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        self._poll_interval_seconds = poll_interval_seconds
        self._poll_task: asyncio.Task | None = None

    def start(self) -> None:
        """Begin the background poll loop - call once at app startup
        (app/main.py's lifespan). Safe to call more than once; only the
        first call has any effect."""
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Call on app shutdown so the loop doesn't outlive the process's
        connection pools (app/main.py's lifespan)."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("learning_loop_scheduler_poll_failed")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _poll_once(self) -> None:
        async with session_scope() as session:
            result = await session.execute(select(Hub).where(Hub.active.is_(True)))
            hubs = list(result.scalars().all())

        for hub in hubs:
            await self.maybe_run_for_hub(hub)

    async def maybe_run_for_hub(self, hub: Hub) -> None:
        """Public so tests can drive one hub directly without waiting on
        the poll loop's real-time clock check."""
        hub_id = str(hub.id)
        try:
            local_now = datetime.now(ZoneInfo(hub.timezone))
        except Exception:
            logger.warning("learning_loop_scheduler_bad_timezone", hub_id=hub_id, timezone=hub.timezone)
            return

        if local_now.hour != NIGHTLY_RUN_LOCAL_HOUR:
            return

        today = local_now.date().isoformat()
        redis = get_client()
        if await redis.get(_last_run_date_key(hub_id)) == today:
            return  # already ran today

        acquired = await redis.set(_lock_key(hub_id), "1", nx=True, ex=LOCK_TTL_SECONDS)
        if not acquired:
            return  # another instance (or task) is already handling this hub's run right now

        try:
            async with session_scope() as session:
                created = await run_nightly_job(session, hub_id=hub_id)
            await redis.set(_last_run_date_key(hub_id), today)
            logger.info(
                "learning_loop_scheduled_run_completed", hub_id=hub_id, proposed_rules_created=len(created)
            )
        except Exception:
            logger.exception("learning_loop_scheduled_run_failed", hub_id=hub_id)
        finally:
            await redis.delete(_lock_key(hub_id))


learning_loop_scheduler = LearningLoopScheduler()
