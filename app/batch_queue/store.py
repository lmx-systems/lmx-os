"""
Redis-backed storage for the Batch-Hold Queue's working set.

Held orders are small and short-lived (minutes, per SLA tier), so we keep
the full working set in a single Redis hash per hub rather than round-
tripping to Postgres on every hold-cycle tick. Postgres `orders.status`
still reflects the current state (held/queued/etc.) for anything that
needs the durable record, but the optimizer's per-cycle read path only
ever talks to Redis.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.batch_queue.queue import HeldOrder
from app.redis_client import get_client, timed_operation


def _queue_key(hub_id: str) -> str:
    return f"holdqueue:{hub_id}:orders"


def _serialize(order: HeldOrder) -> str:
    return json.dumps(
        {
            "order_id": order.order_id,
            "shop_lat": order.shop_lat,
            "shop_lng": order.shop_lng,
            "sla_tier": order.sla_tier,
            "hold_deadline": order.hold_deadline.isoformat(),
            "held_since": order.held_since.isoformat(),
        }
    )


def _deserialize(raw: str) -> HeldOrder:
    data = json.loads(raw)
    return HeldOrder(
        order_id=data["order_id"],
        shop_lat=data["shop_lat"],
        shop_lng=data["shop_lng"],
        sla_tier=data["sla_tier"],
        hold_deadline=datetime.fromisoformat(data["hold_deadline"]),
        held_since=datetime.fromisoformat(data["held_since"]),
    )


class HoldQueueStore:
    def __init__(self) -> None:
        self._redis = get_client()

    async def add(self, hub_id: str, order: HeldOrder) -> None:
        async with timed_operation("holdqueue.add"):
            await self._redis.hset(_queue_key(hub_id), order.order_id, _serialize(order))

    async def remove(self, hub_id: str, order_id: str) -> None:
        async with timed_operation("holdqueue.remove"):
            await self._redis.hdel(_queue_key(hub_id), order_id)

    async def get_all(self, hub_id: str) -> list[HeldOrder]:
        async with timed_operation("holdqueue.get_all"):
            raw = await self._redis.hgetall(_queue_key(hub_id))
        return [_deserialize(v) for v in raw.values()]
