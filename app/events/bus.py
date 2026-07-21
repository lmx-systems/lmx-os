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
import uuid
from collections.abc import Awaitable, Callable

import structlog

from app.redis_client import get_client

logger = structlog.get_logger(__name__)

HubEventHandler = Callable[[str], Awaitable[None]]

DIRTY_HUBS_KEY = "events:dirty_hubs"
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
# Comfortably longer than the design doc's 5s optimizer cycle budget (or
# any other handler this bus might end up running), so a lock is never
# released out from under a still-running handler by its own expiry
# except in a genuine crash/hang - the case it exists to recover from.
LOCK_TTL_SECONDS = 60


def _lock_key(hub_id: str) -> str:
    return f"events:running:{hub_id}"


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
        dirty_hub_ids = await redis.smembers(DIRTY_HUBS_KEY)
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
