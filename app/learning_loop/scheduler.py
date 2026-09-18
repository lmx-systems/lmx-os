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
from app.hub_calendar import is_hub_closed_on
from app.identity import propose_duplicate_locations, refresh_hub_dwell_statistics
from app.record.consequences import close_consequence_windows
from app.record.linkage import run_linkage_detectors
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


def _global_job_key(name: str, day: str) -> str:
    """A once-a-day claim for work that is not per hub.

    `propose_duplicate_locations` compares every dock against every other and
    is deliberately not hub-scoped: the same physical dock can be reached from
    two hubs, and scoping the comparison would make exactly that pair - the
    most valuable merge to catch - invisible.

    So it must run once a night, not once per hub. `set nx` on a day key is the
    claim: whichever hub's tick fires first does the work and the rest skip it.
    """
    return f"learning_loop:global:{name}:{day}"


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
                # Skip the nightly job on a day the hub wasn't operating (R6)
                # - a closed day has no delivery activity for the pattern
                # detector to learn from. Still marked as "run" below so the
                # scheduler doesn't retry it all day.
                if await is_hub_closed_on(session, hub_id, local_now.date()):
                    logger.info("learning_loop_skipped_hub_closed", hub_id=hub_id)
                    created = []
                else:
                    created = await run_nightly_job(session, hub_id=hub_id)

                # IDN-4's dwell statistics, refreshed on the same nightly tick.
                # `refresh_dwell_statistics` existed and nothing called it, so
                # every profile's figures were whatever a one-off script last
                # left there - and MODEL_AND_DATA_BRIEF specifies M1 to read
                # them (docs/ROADMAP_AUDIT_2026-09.md).
                #
                # Outside the closed-day branch above deliberately: a hub that
                # was shut today still has yesterday's stops to compute from,
                # and the pattern detector's reason for skipping - no activity
                # to learn from - does not apply to recomputing a percentile
                # over history.
                #
                # Its own try, because a dwell refresh must not cost the hub its
                # rule proposals, and the rule proposals must not cost it the
                # refresh.
                try:
                    docks = await refresh_hub_dwell_statistics(session, hub_id=hub_id)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    logger.exception("dwell_refresh_failed", hub_id=hub_id)
                    docks = 0

                # REC-2's silence half. `close_consequence_windows` records
                # "nothing happened" for every late order nobody judged inside
                # the window - which is the half of the label set that nobody
                # volunteers, because somebody always reports the angry phone
                # call and nobody reports the twelve deliveries that were late
                # and fine.
                #
                # **Only safe because the judging surface now exists.** With no
                # way to record a real consequence, this would label every late
                # delivery as silence: false labels, in an append-only ledger,
                # indistinguishable from true ones by the time anyone trained on
                # them. See POST /orders/{id}/consequence.
                try:
                    silences = await close_consequence_windows(session, hub_id=hub_id)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    logger.exception("consequence_close_failed", hub_id=hub_id)
                    silences = 0

                # REC-4's three detectors. They raise questions for a dispatcher
                # rather than findings, and `GET /operations/linkage-flags` is
                # what reads them - running them without that would fill a table
                # nobody opens, which is not better than not running them.
                try:
                    flags = await run_linkage_detectors(session, hub_id=hub_id)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    logger.exception("linkage_detectors_failed", hub_id=hub_id)
                    flags = {}

                # IDN-2's producer. Until now the merge review queue had nothing
                # feeding it, so "human-confirms the founding ~230" could not
                # happen - the queue was permanently empty
                # (docs/ROADMAP_AUDIT_2026-09.md).
                #
                # Claimed once a day across all hubs rather than run per hub:
                # the comparison is global on purpose, because the same physical
                # dock can be reached from two hubs and that is the most valuable
                # pair to catch. Running it per hub would do the same global work
                # once per hub and still miss nothing extra.
                merges = 0
                if await redis.set(
                    _global_job_key("propose_merges", today), "1", nx=True, ex=86400
                ):
                    try:
                        merges = len(await propose_duplicate_locations(session))
                        await session.commit()
                    except Exception:
                        await session.rollback()
                        logger.exception("merge_proposal_failed")
            await redis.set(_last_run_date_key(hub_id), today)
            logger.info(
                "learning_loop_scheduled_run_completed",
                hub_id=hub_id,
                proposed_rules_created=len(created),
                docks_refreshed=docks,
                silences_recorded=silences,
                linkage_flags_raised=sum(flags.values()),
                merges_proposed=merges,
            )
        except Exception:
            logger.exception("learning_loop_scheduled_run_failed", hub_id=hub_id)
        finally:
            await redis.delete(_lock_key(hub_id))


learning_loop_scheduler = LearningLoopScheduler()
