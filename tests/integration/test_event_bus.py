"""
HubEventBus (app/events/bus.py) - now Redis-backed (docs/ROADMAP.md E8),
so this moved from the plain offline tests/ suite to here. Drives
._poll_once() directly rather than starting the real background loop and
waiting on real sleeps - deterministic and fast, and exactly how the loop
itself drives it internally (see _poll_loop).
"""
import asyncio

import pytest

from app.events.bus import DIRTY_HUBS_KEY, HubEventBus, _lock_key

pytestmark = pytest.mark.integration


async def test_publish_marks_a_hub_dirty_and_poll_runs_the_handler_once(real_redis_client):
    calls = []

    async def handler(hub_id):
        calls.append(hub_id)

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await bus._poll_once()
    await bus.wait_idle()

    assert calls == ["hub-1"]


async def test_concurrent_publishes_for_same_hub_coalesce_into_one_rerun(real_redis_client):
    calls = []
    gate = asyncio.Event()

    async def handler(hub_id):
        calls.append(hub_id)
        await gate.wait()

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await bus._poll_once()  # claims the hub, spawns a task that blocks on the gate

    # A burst of events while the first run is still in flight collapses
    # into a single re-marking of "dirty" (SADD is idempotent), not a run
    # each - and a poll while the lock is still held must not spawn a
    # second run for the same hub.
    await bus.publish("hub-1", "order_held")
    await bus.publish("hub-1", "driver_status_changed")
    await bus.publish("hub-1", "order_held")
    await bus._poll_once()
    assert len(calls) == 1

    gate.set()
    await bus.wait_idle()
    # The lock's now released and the hub was re-marked dirty during the
    # run above - one more poll should pick it up as the coalesced rerun.
    await bus._poll_once()
    await bus.wait_idle()

    assert calls == ["hub-1", "hub-1"]


async def test_different_hubs_run_independently(real_redis_client):
    calls = []

    async def handler(hub_id):
        calls.append(hub_id)

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await bus.publish("hub-2", "order_held")
    await bus._poll_once()
    await bus.wait_idle()

    assert sorted(calls) == ["hub-1", "hub-2"]


async def test_handler_exception_releases_the_lock_and_does_not_wedge_the_hub(real_redis_client):
    calls = []

    async def handler(hub_id):
        calls.append(hub_id)
        if len(calls) == 1:
            raise RuntimeError("boom")

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await bus._poll_once()
    await bus.wait_idle()

    # A prior failure must not leave the hub stuck as permanently "running".
    await bus.publish("hub-1", "order_held")
    await bus._poll_once()
    await bus.wait_idle()

    assert calls == ["hub-1", "hub-1"]


async def test_a_second_instance_cannot_claim_a_hub_already_locked_by_the_first(real_redis_client):
    """The actual bug this whole redesign closes: two separate processes
    (modeled here as two separate HubEventBus instances sharing the same
    real Redis) must never both run the same hub's handler concurrently."""
    calls = []
    gate = asyncio.Event()

    async def slow_handler(hub_id):
        calls.append(("slow", hub_id))
        await gate.wait()

    async def fast_handler(hub_id):
        calls.append(("fast", hub_id))

    instance_a = HubEventBus(slow_handler)
    instance_b = HubEventBus(fast_handler)

    await instance_a.publish("hub-1", "order_held")
    await instance_a._poll_once()  # instance A claims and starts running hub-1

    # Instance B polls the same dirty-hubs/lock state in the same real
    # Redis - hub-1 is no longer even in the dirty set (instance A
    # consumed it), and even if it were, the lock instance A holds would
    # block instance B from claiming it.
    await instance_b._poll_once()
    assert ("fast", "hub-1") not in calls

    gate.set()
    await instance_a.wait_idle()
    assert calls == [("slow", "hub-1")]


async def test_lock_key_is_released_after_a_run_completes(real_redis_client):
    async def handler(hub_id):
        return None

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await bus._poll_once()
    await bus.wait_idle()

    remaining = await real_redis_client.get(_lock_key("hub-1"))
    assert remaining is None


async def test_dirty_hubs_set_is_empty_after_a_successful_run(real_redis_client):
    async def handler(hub_id):
        return None

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await bus._poll_once()
    await bus.wait_idle()

    remaining = await real_redis_client.smembers(DIRTY_HUBS_KEY)
    assert "hub-1" not in remaining
