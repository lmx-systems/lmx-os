"""
Generic in-process event bus for triggering per-hub work off real events
instead of polling or manual triggering.

Deliberately in-process rather than a durable external bus (e.g. Redis
pub/sub): every event source that exists today (order ingestion, fleet
state updates) already runs inside this same FastAPI process, so a network
hop would buy nothing and it keeps Phase 1 free of another moving part. If
a future phase splits event producers and consumers across processes,
`publish`/handler is the seam to swap for a durable bus without touching
callers.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog

logger = structlog.get_logger(__name__)

HubEventHandler = Callable[[str], Awaitable[None]]


class HubEventBus:
    """
    Runs `handler(hub_id)` off published events, debounced per hub: a burst
    of events for the same hub (e.g. several orders ingested in the same
    second) collapses into one running call plus at most one coalesced
    rerun queued right behind it, rather than one call per event. This
    keeps a slow handler (like a dispatch cycle with its own cycle-time
    budget) from piling up concurrent runs under an event storm, while
    guaranteeing nothing that arrived during a running call is dropped -
    it's picked up by the rerun.
    """

    def __init__(self, handler: HubEventHandler) -> None:
        self._handler = handler
        self._running: set[str] = set()
        self._pending: set[str] = set()
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    async def publish(self, hub_id: str, event_type: str) -> None:
        logger.info("hub_event_published", hub_id=hub_id, event_type=event_type)
        async with self._lock:
            if hub_id in self._running:
                self._pending.add(hub_id)
                return
            self._running.add(hub_id)
        task = asyncio.create_task(self._run_and_release(hub_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_and_release(self, hub_id: str) -> None:
        try:
            await self._handler(hub_id)
        except Exception:
            logger.exception("hub_event_handler_failed", hub_id=hub_id)
        finally:
            async with self._lock:
                self._running.discard(hub_id)
                rerun = hub_id in self._pending
                self._pending.discard(hub_id)
            if rerun:
                await self.publish(hub_id, "coalesced_rerun")

    async def wait_idle(self) -> None:
        """Await every in-flight/queued run - used on app shutdown so a run
        in progress isn't abruptly cancelled mid-way."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
