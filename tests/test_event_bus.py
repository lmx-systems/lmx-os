import asyncio

import pytest

from app.events.bus import HubEventBus


@pytest.mark.asyncio
async def test_publish_runs_handler_once_for_a_single_event():
    calls = []

    async def handler(hub_id):
        calls.append(hub_id)

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await bus.wait_idle()

    assert calls == ["hub-1"]


@pytest.mark.asyncio
async def test_concurrent_publishes_for_same_hub_coalesce_into_one_rerun():
    calls = []
    gate = asyncio.Event()

    async def handler(hub_id):
        calls.append(hub_id)
        await gate.wait()

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await asyncio.sleep(0)  # let the task start and block on the gate

    # A burst of events while the first run is still in flight should
    # collapse into a single queued rerun, not a run each.
    await bus.publish("hub-1", "order_held")
    await bus.publish("hub-1", "driver_status_changed")
    await bus.publish("hub-1", "order_held")
    assert len(calls) == 1

    gate.set()
    await bus.wait_idle()

    assert calls == ["hub-1", "hub-1"]


@pytest.mark.asyncio
async def test_different_hubs_run_independently():
    calls = []

    async def handler(hub_id):
        calls.append(hub_id)

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await bus.publish("hub-2", "order_held")
    await bus.wait_idle()

    assert sorted(calls) == ["hub-1", "hub-2"]


@pytest.mark.asyncio
async def test_handler_exception_does_not_propagate_or_wedge_the_hub():
    calls = []

    async def handler(hub_id):
        calls.append(hub_id)
        if len(calls) == 1:
            raise RuntimeError("boom")

    bus = HubEventBus(handler)
    await bus.publish("hub-1", "order_held")
    await bus.wait_idle()

    # A prior failure must not leave the hub stuck as permanently "running".
    await bus.publish("hub-1", "order_held")
    await bus.wait_idle()

    assert calls == ["hub-1", "hub-1"]
