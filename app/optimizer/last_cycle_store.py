"""
Tiny Redis snapshot of the most recently completed Dispatch Optimizer
cycle per hub.

Why this exists: dispatch cycles run automatically off events (order
ingested, driver status changed - see app/optimizer/event_trigger.py) with
no push channel to any dashboard, and the manual POST /run-cycle endpoint
only ever returns its result to whoever called it. Without this, a
dashboard has no way to show "last cycle" info for the (common) case
where nobody in the browser triggered it - it would either lie by only
reflecting manual triggers, or show nothing at all. One JSON blob per hub,
overwritten every cycle, is all that's needed - this is a glanceable
"is the system alive" signal, not an audit log.
"""
from __future__ import annotations

from app.redis_client import get_client, timed_operation
from app.schemas.optimizer import LastCycleSnapshot

# A week is generous for a demo/dev hub that stops seeing traffic - just
# bounds how long an abandoned hub_id's key lingers, nothing functional
# depends on it expiring.
SNAPSHOT_TTL_SECONDS = 7 * 24 * 3600


def _key(hub_id: str) -> str:
    return f"optimizer:{hub_id}:last_cycle"


class LastCycleStore:
    def __init__(self) -> None:
        self._redis = get_client()

    async def set(self, snapshot: LastCycleSnapshot) -> None:
        async with timed_operation("optimizer.last_cycle.set"):
            await self._redis.set(
                _key(snapshot.hub_id),
                snapshot.model_dump_json(),
                ex=SNAPSHOT_TTL_SECONDS,
            )

    async def get(self, hub_id: str) -> LastCycleSnapshot | None:
        async with timed_operation("optimizer.last_cycle.get"):
            raw = await self._redis.get(_key(hub_id))
        if raw is None:
            return None
        return LastCycleSnapshot.model_validate_json(raw)
