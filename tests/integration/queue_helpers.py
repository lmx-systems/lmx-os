"""Helpers for tests that need the batch-hold queue to let an order go."""
import dataclasses
from datetime import datetime, timedelta, timezone

from app.batch_queue.store import HoldQueueStore


async def let_the_hold_run_out(hub_id) -> None:
    """Move every held order's deadline into the past, so the next cycle
    dispatches it.

    A lone order waits for a partner until its deadline (the design doc's
    Section 6), so a test that needs one dispatched lets its hold run out - the
    reason a lone order really goes out alone - rather than adding a partner it
    would then have to route around.
    """
    store = HoldQueueStore()
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    for held in await store.get_all(str(hub_id)):
        await store.add(str(hub_id), dataclasses.replace(held, hold_deadline=past))
