"""
Redis-backed hub event bus (roadmap item E8) - the multi-instance
counterpart to app/events/bus.py's in-process HubEventBus.

Why this exists: the in-process bus only triggers handlers inside the
process that published the event. The moment the app runs as more than
one instance (a real hosting setup, roadmap item S3), an order ingested
on instance A would never trigger a dispatch cycle if instance B is the
one that should run it - silently. This bus moves the event transport to
Redis pub/sub so every instance sees every event, then uses two small
Redis claims to keep the semantics the in-process bus already guarantees:

1. Per-event dedupe (SET NX on the event id): every instance receives
   each pub/sub message, exactly one wins the claim and processes it.
2. Per-hub run lock + pending marker: while a cycle for hub X runs
   (anywhere), further events for X collapse into a single queued rerun -
   the same "one running + at most one coalesced rerun" debounce as the
   in-process bus, now cluster-wide.

Selected via EVENT_BUS_BACKEND=redis (app/config.py); the in-process bus
remains the default for single-instance deployments and local dev, where
a network hop buys nothing.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import structlog

from app.events.bus import HubEventHandler
from app.redis_client import get_client

logger = structlog.get_logger(__name__)

CHANNEL = "hub_events"
EVENT_CLAIM_TTL_SECONDS = 5 * 60
# Generously above the optimizer's cycle budget (5s) - this is a crash
# backstop so a dead instance can't wedge a hub forever, not a timing
# assumption. If the run finishes normally the lock is deleted explicitly.
RUN_LOCK_TTL_SECONDS = 120


def _event_key(event_id: str) -> str:
    return f"hub_events:claimed:{event_id}"


def _run_lock_key(hub_id: str) -> str:
    return f"hub_events:running:{hub_id}"


def _pending_key(hub_id: str) -> str:
    return f"hub_events:pending:{hub_id}"


class RedisHubEventBus:
    """Same interface as HubEventBus (publish / wait_idle), plus
    start()/stop() for the pub/sub listener task."""

    def __init__(self, handler: HubEventHandler) -> None:
        self._handler = handler
        self._listen_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._pubsub = None

    async def publish(self, hub_id: str, event_type: str) -> None:
        event_id = str(uuid.uuid4())
        logger.info("hub_event_published", hub_id=hub_id, event_type=event_type, event_id=event_id)
        await get_client().publish(
            CHANNEL, json.dumps({"event_id": event_id, "hub_id": hub_id, "event_type": event_type})
        )

    async def start(self) -> None:
        self._pubsub = get_client().pubsub()
        await self._pubsub.subscribe(CHANNEL)
        self._listen_task = asyncio.create_task(self._listen(), name="hub-event-bus-listener")
        logger.info("redis_event_bus_listening", channel=CHANNEL)

    async def stop(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._pubsub is not None:
            await self._pubsub.unsubscribe(CHANNEL)
            await self._pubsub.aclose()
            self._pubsub = None

    async def _listen(self) -> None:
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                await self._on_event(data["event_id"], data["hub_id"], data.get("event_type", ""))
            except Exception:  # noqa: BLE001 - one bad message must not kill the listener
                logger.warning("hub_event_message_failed", exc_info=True)

    async def _on_event(self, event_id: str, hub_id: str, event_type: str) -> None:
        redis = get_client()
        # Exactly one instance processes each event.
        claimed = await redis.set(_event_key(event_id), "1", nx=True, ex=EVENT_CLAIM_TTL_SECONDS)
        if not claimed:
            return
        task = asyncio.create_task(self._execute(hub_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _execute(self, hub_id: str) -> None:
        redis = get_client()
        acquired = await redis.set(_run_lock_key(hub_id), "1", nx=True, ex=RUN_LOCK_TTL_SECONDS)
        if not acquired:
            # A run for this hub is already underway somewhere in the
            # cluster - queue (at most) one rerun behind it, same
            # coalescing the in-process bus does with its _pending set.
            await redis.set(_pending_key(hub_id), "1", ex=RUN_LOCK_TTL_SECONDS)
            return

        try:
            await self._handler(hub_id)
        except Exception:  # noqa: BLE001 - mirror HubEventBus: log, never raise
            logger.exception("hub_event_handler_failed", hub_id=hub_id)
        finally:
            await redis.delete(_run_lock_key(hub_id))
            rerun = await redis.delete(_pending_key(hub_id))
        if rerun:
            await self._execute(hub_id)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
