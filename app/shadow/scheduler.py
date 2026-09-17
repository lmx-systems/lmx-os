"""Run the shadow cycle through the day, so `DEC-0` has a day to report on.

`app/shadow/divergence.py` computes the delta and `app/shadow/recorder.py`
produces the rows it reads. Until this existed, nothing produced them:
`record_shadow_cycle` had no caller anywhere in the codebase, so the shadow log
was empty as well as unread.

Same shape as `app/learning_loop/scheduler.py` - an asyncio loop with a Redis
lock per hub, started from the app's lifespan - because that convention is
already here and a scheduling library for "every few minutes" would be a poor
trade. The differences are all consequences of this running many times a day
rather than once.

**Off by default, and the reason is money.** Every cycle calls
`plan_cycle`, and once `DEC-3` points the optimizer at a live Google project
that is a billed API call. An unattended loop making one every few minutes, per
hub, around the clock, is a cost nobody chose - and it would arrive as a bill
rather than as a decision. So `shadow_scheduler_enabled` defaults to false and
turning it on is deliberate. Nothing about shadow mode is unsafe; it writes no
operational state. It is not free.

**Cadence is the load-bearing setting, not a tuning knob.** The dispatch lead
the divergence report measures is bounded below by how often this fires: a cycle
every thirty minutes cannot demonstrate beating a dispatcher who inserts an
order the moment it lands, and on a HOT_SHOT tier - two minutes - it cannot
demonstrate anything at all. The report says so out loud by measuring the
interval actually achieved rather than trusting this number, because a process
that restarted, or a hub whose lock was held, produces gaps the configuration
does not know about.

**Operating hours only.** A cycle at three in the morning plans an empty queue
and records that it decided nothing, which is true and useless: it dilutes every
rate in the report with rows that never had work to do. `ShadowDecision` already
distinguishes a closed hub from an idle one for the same reason.

**A closed day is skipped entirely**, the same rule the learning loop follows
(`R6`). A hub that was not operating produced no dispatcher decisions, so there
is nothing for the shadow plan to have disagreed with.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.hub_calendar import is_hub_closed_on
from app.models.hub import Hub
from app.redis_client import get_client
from app.shadow.recorder import record_shadow_cycle

logger = structlog.get_logger(__name__)

# Long enough that a slow solve does not wedge a hub out of its next cycle,
# short enough that a crashed run does not hold the lock past the cadence.
LOCK_TTL_SECONDS = 240


def _last_run_key(hub_id: str) -> str:
    return f"shadow:last_run_at:{hub_id}"


def _lock_key(hub_id: str) -> str:
    return f"shadow:cycle_running:{hub_id}"


class ShadowScheduler:
    """Fires `record_shadow_cycle` per hub, on a cadence, changing nothing."""

    def __init__(
        self,
        *,
        cadence_seconds: float | None = None,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self._cadence_seconds = cadence_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._poll_task: asyncio.Task | None = None

    @property
    def cadence_seconds(self) -> float:
        if self._cadence_seconds is not None:
            return self._cadence_seconds
        return float(settings.shadow_cycle_cadence_seconds)

    def start(self) -> None:
        """Begin the loop - called once from `app/main.py`'s lifespan.

        Declines to start when disabled, and says so at info rather than
        silently doing nothing: a shadow log that is empty because the feature
        is off looks identical to one that is empty because it is broken.
        """
        if not settings.shadow_scheduler_enabled:
            logger.info(
                "shadow_scheduler_disabled",
                reason="shadow_scheduler_enabled is false; no cycles will be recorded",
            )
            return
        if self._poll_task is None:
            logger.info(
                "shadow_scheduler_started", cadence_seconds=self.cadence_seconds
            )
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
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
                logger.exception("shadow_scheduler_poll_failed")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _poll_once(self) -> None:
        async with session_scope() as session:
            hubs = list(
                (await session.execute(select(Hub).where(Hub.active.is_(True))))
                .scalars()
                .all()
            )
        for hub in hubs:
            await self.maybe_run_for_hub(hub)

    def _within_operating_hours(self, local_now: datetime) -> bool:
        start = settings.shadow_cycle_start_local_hour
        end = settings.shadow_cycle_end_local_hour
        return start <= local_now.hour < end

    async def maybe_run_for_hub(self, hub: Hub) -> bool:
        """One hub's turn. Public so a test can drive it without the clock.

        Returns whether a cycle was recorded, which is what the tests assert on
        - `record_shadow_cycle` returning a row is not the same as the scheduler
        having decided to call it.
        """
        hub_id = str(hub.id)
        try:
            local_now = datetime.now(ZoneInfo(hub.timezone))
        except Exception:
            logger.warning(
                "shadow_scheduler_bad_timezone", hub_id=hub_id, timezone=hub.timezone
            )
            return False

        if not self._within_operating_hours(local_now):
            return False

        redis = get_client()
        # Checked against what was last *recorded*, not against a timer, so a
        # process restart cannot double the cadence and a second instance
        # cannot halve it.
        last = await redis.get(_last_run_key(hub_id))
        now = datetime.now(timezone.utc)
        if last:
            try:
                elapsed = (now - datetime.fromisoformat(last)).total_seconds()
            except ValueError:
                elapsed = None
            if elapsed is not None and elapsed < self.cadence_seconds:
                return False

        acquired = await redis.set(_lock_key(hub_id), "1", nx=True, ex=LOCK_TTL_SECONDS)
        if not acquired:
            return False

        try:
            async with session_scope() as session:
                if await is_hub_closed_on(session, hub_id, local_now.date()):
                    # Marked as run so a closed day is not retried every poll,
                    # and recorded nowhere: a hub that was not operating made no
                    # dispatcher decisions for a shadow plan to disagree with.
                    await redis.set(_last_run_key(hub_id), now.isoformat())
                    logger.info("shadow_cycle_skipped_hub_closed", hub_id=hub_id)
                    return False
                decision = await record_shadow_cycle(session, hub_id)
            await redis.set(_last_run_key(hub_id), now.isoformat())
            logger.info(
                "shadow_cycle_scheduled_run_completed",
                hub_id=hub_id,
                engine=decision.engine,
                assigned=decision.assigned_order_count,
                unassigned=decision.unassigned_order_count,
            )
            return True
        except Exception:
            # The last-run marker is deliberately not set on failure, so the
            # next poll retries rather than waiting out a full cadence on a
            # transient solver error.
            logger.exception("shadow_cycle_scheduled_run_failed", hub_id=hub_id)
            return False
        finally:
            await redis.delete(_lock_key(hub_id))


shadow_scheduler = ShadowScheduler()
