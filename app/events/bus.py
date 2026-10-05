"""
Distributed per-hub event bus (docs/ROADMAP.md E8) - triggers per-hub work
off real events (order held, driver status change, stop completed)
instead of polling or manual triggering.

The original in-process design only worked within a single running
process: an event published on one instance was invisible to every other
instance, so multi-instance re-optimization silently stopped happening
for events raised on whichever instance *didn't* handle a given request.
This version coordinates through Redis instead of local asyncio state,
matching how FleetStateManager/HoldQueueStore already treat Redis (not
per-process memory) as the durable, shared source of truth.

Same debounce/coalesce contract as before - a burst of events for the
same hub collapses into at most one running call plus one coalesced
rerun, never one call per raw event - now enforced across every instance
at once, not just within one process:

  - `events:dirty_hubs` (Redis SET) - hub_ids with unprocessed events.
    SADD is idempotent, so a hub already marked dirty just stays marked
    once no matter how many instances see how many events for it - that
    *is* the coalescing, for free, across the whole fleet of instances.
  - `events:running:{hub_id}` (a SET NX EX key) - a distributed lock. Only
    the instance that successfully claims this may run the handler for
    that hub; every other instance backs off and leaves the hub marked
    dirty for a later poll (by itself or by whichever instance releases
    the lock next).

  - `events:wakeups` (Redis sorted set) - work that becomes due with time
    rather than with an event, scored by when. Each poll moves what is due
    into the dirty set. The batch-hold queue schedules one per held order
    at its hold deadline: a lone order waits for a partner until exactly
    then (the design doc's Section 6, "hold deadline reached for any
    order"), and without this it waited for the next event at its hub or
    the five-minute sweep.

No pub/sub wake-up in this pass - a plain fixed-interval poll picks up
newly-dirty hubs. That trades a small bounded latency (at most one poll
interval) for far less moving parts than a persistent per-instance
pub/sub subscriber with its own reconnect handling; add that later if the
added latency ever actually threatens the design doc's 5s cycle budget -
it doesn't at the default interval below.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog

from app.redis_client import as_text, get_client

logger = structlog.get_logger(__name__)

HubEventHandler = Callable[[str], Awaitable[None]]

DIRTY_HUBS_KEY = "events:dirty_hubs"
WAKEUPS_KEY = "events:wakeups"
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
# Comfortably longer than the design doc's 5s optimizer cycle budget (or
# any other handler this bus might end up running), so a lock is never
# released out from under a still-running handler by its own expiry
# except in a genuine crash/hang - the case it exists to recover from.
LOCK_TTL_SECONDS = 60


def _lock_key(hub_id: str) -> str:
    return f"events:running:{hub_id}"


def wakeup_member(hub_id: str, token: str) -> str:
    """One wake-up's entry in `WAKEUPS_KEY`. `token` tells one hub's apart -
    the hold queue uses the order id, so re-adding an order moves its wake-up
    rather than adding a second."""
    return f"{hub_id}|{token}"


class HubEventBus:
    """
    Runs `handler(hub_id)` off published events. See this module's
    docstring for the Redis-backed coordination this relies on to behave
    correctly across more than one process.
    """

    def __init__(self, handler: HubEventHandler, poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        self._handler = handler
        self._poll_interval_seconds = poll_interval_seconds
        self._instance_id = uuid.uuid4().hex
        self._local_running: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._poll_task: asyncio.Task | None = None

    async def publish(self, hub_id: str, event_type: str) -> None:
        logger.info("hub_event_published", hub_id=hub_id, event_type=event_type)
        await get_client().sadd(DIRTY_HUBS_KEY, hub_id)

    def start(self) -> None:
        """Begin the background poll loop - call once at app startup
        (app/main.py's lifespan). Safe to call more than once; only the
        first call has any effect."""
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("hub_event_poll_failed")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _poll_once(self) -> None:
        redis = get_client()
        await self._wake_due_hubs(redis)
        dirty_hub_ids = [as_text(hub_id) for hub_id in await redis.smembers(DIRTY_HUBS_KEY)]
        for hub_id in dirty_hub_ids:
            if hub_id in self._local_running:
                continue  # this process already has a task running for it
            acquired = await redis.set(_lock_key(hub_id), self._instance_id, nx=True, ex=LOCK_TTL_SECONDS)
            if not acquired:
                continue  # another instance (or another task in this one, from a prior tick) owns it right now

            # Consumed the dirty signal we're about to satisfy - a new
            # event published *during* the run below correctly re-marks
            # the hub dirty for a future poll, since it's no longer here.
            await redis.srem(DIRTY_HUBS_KEY, hub_id)

            self._local_running.add(hub_id)
            task = asyncio.create_task(self._run_and_release(hub_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _wake_due_hubs(self, redis) -> None:
        """Mark dirty every hub with a wake-up that has come due.

        ZREM's count says which instance claimed a wake-up, so with several
        polling, only one marks the hub - and marking it twice would be
        harmless anyway, since the dirty set coalesces.
        """
        for raw in await redis.zrangebyscore(WAKEUPS_KEY, "-inf", time.time()):
            member = as_text(raw)
            if await redis.zrem(WAKEUPS_KEY, member):
                await redis.sadd(DIRTY_HUBS_KEY, member.split("|", 1)[0])

    async def _run_and_release(self, hub_id: str) -> None:
        try:
            await self._handler(hub_id)
        except Exception:
            logger.exception("hub_event_handler_failed", hub_id=hub_id)
        finally:
            self._local_running.discard(hub_id)
            await get_client().delete(_lock_key(hub_id))

    async def wait_idle(self) -> None:
        """Stop accepting new runs and await every run this *instance*
        started - used on app shutdown so one in progress isn't abruptly
        cancelled mid-way. Doesn't wait on other instances' runs; nothing
        about shutting down this process should block on those."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
