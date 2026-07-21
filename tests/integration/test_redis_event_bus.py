"""
RedisHubEventBus (roadmap item E8) against real Redis: two bus instances
simulate two app processes. Exactly one processes each published event,
and per-hub runs coalesce cluster-wide the same way the in-process bus
coalesces locally.
"""
import asyncio

import pytest

from app.events.redis_bus import RedisHubEventBus

pytestmark = pytest.mark.integration


class _RecordingHandler:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[str] = []
        self.delay = delay

    async def __call__(self, hub_id: str) -> None:
        self.calls.append(hub_id)
        if self.delay:
            await asyncio.sleep(self.delay)


async def _drain(*buses: RedisHubEventBus) -> None:
    # Give pub/sub delivery a beat, then wait for handler tasks to finish.
    await asyncio.sleep(0.3)
    for bus in buses:
        await bus.wait_idle()


async def test_event_published_on_one_instance_runs_on_exactly_one(real_redis_client):
    handler_a, handler_b = _RecordingHandler(), _RecordingHandler()
    bus_a, bus_b = RedisHubEventBus(handler_a), RedisHubEventBus(handler_b)
    await bus_a.start()
    await bus_b.start()
    try:
        await bus_a.publish("hub-e8-1", "order_held")
        await _drain(bus_a, bus_b)
    finally:
        await bus_a.stop()
        await bus_b.stop()

    total_calls = handler_a.calls + handler_b.calls
    assert total_calls == ["hub-e8-1"]  # exactly once, across both instances


async def test_burst_of_events_coalesces_into_run_plus_one_rerun(real_redis_client):
    # One slow handler instance; events published while its run is in
    # flight collapse into a single queued rerun - not one run per event.
    handler = _RecordingHandler(delay=0.4)
    bus = RedisHubEventBus(handler)
    await bus.start()
    try:
        for _ in range(5):
            await bus.publish("hub-e8-2", "order_held")
        await asyncio.sleep(0.3)  # let claims land while run 1 is still going
        await bus.wait_idle()
    finally:
        await bus.stop()

    # 5 events -> first run + one coalesced rerun (2 calls), never 5.
    assert 1 <= len(handler.calls) <= 2
    assert set(handler.calls) == {"hub-e8-2"}


async def test_different_hubs_run_independently(real_redis_client):
    handler = _RecordingHandler()
    bus = RedisHubEventBus(handler)
    await bus.start()
    try:
        await bus.publish("hub-e8-3a", "order_held")
        await bus.publish("hub-e8-3b", "driver_status_changed")
        await _drain(bus)
    finally:
        await bus.stop()

    assert sorted(handler.calls) == ["hub-e8-3a", "hub-e8-3b"]
