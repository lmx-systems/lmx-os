"""
Redis-backed storage for the Batch-Hold Queue's working set.

Held orders are small and short-lived (minutes, per SLA tier), so we keep
the full working set in a single Redis hash per hub rather than round-
tripping to Postgres on every hold-cycle tick. Postgres `orders.status`
still reflects the current state (held/queued/etc.) for anything that
needs the durable record. The optimizer reads the working set from here,
and reads one thing per cycle from Postgres: which of these orders a
dispatcher released. That is the order's status, committed together with
the override's reason, so it isn't copied into this hash.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.batch_queue.queue import HeldOrder
from app.events.bus import WAKEUPS_KEY, wakeup_member
from app.redis_client import as_text, get_client, timed_operation


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
            "shop_name": order.shop_name,
            "delivery_lat": order.delivery_lat,
            "delivery_lng": order.delivery_lng,
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
        # .get(): rows written before this field existed won't have it -
        # falls back to "" rather than KeyError-ing on old Redis data.
        shop_name=data.get("shop_name", ""),
        # Same reasoning, and it matters more here than for shop_name: orders
        # already sitting in the queue at deploy time were written without these,
        # so a KeyError would strand every one of them.
        delivery_lat=data.get("delivery_lat"),
        delivery_lng=data.get("delivery_lng"),
    )


class HoldQueueStore:
    def __init__(self) -> None:
        self._redis = get_client()

    async def add(self, hub_id: str, order: HeldOrder) -> None:
        async with timed_operation("holdqueue.add"):
            await self._redis.hset(_queue_key(hub_id), order.order_id, _serialize(order))
            # Look at the hub again the moment this hold runs out. A lone order
            # waits for a partner until exactly then, and no event marks it.
            await self._redis.zadd(
                WAKEUPS_KEY,
                {wakeup_member(hub_id, order.order_id): order.hold_deadline.timestamp()},
            )

    async def add_if_absent(self, hub_id: str, order: HeldOrder) -> bool:
        """Add an order only if it isn't queued already; True if it was added.

        For the rebuild from Postgres (app/optimizer/redis_rebuild.py), which
        must never overwrite a live entry: that one carries the real
        `held_since` and wake-up, and may have been written after the rebuild
        read Postgres.
        """
        async with timed_operation("holdqueue.add_if_absent"):
            added = await self._redis.hsetnx(_queue_key(hub_id), order.order_id, _serialize(order))
            if added:
                await self._redis.zadd(
                    WAKEUPS_KEY,
                    {wakeup_member(hub_id, order.order_id): order.hold_deadline.timestamp()},
                    nx=True,
                )
        return bool(added)

    async def order_ids(self, hub_id: str) -> set[str]:
        """Which orders are queued, without reading them."""
        async with timed_operation("holdqueue.order_ids"):
            return {as_text(key) for key in await self._redis.hkeys(_queue_key(hub_id))}

    async def remove(self, hub_id: str, order_id: str) -> None:
        async with timed_operation("holdqueue.remove"):
            await self._redis.hdel(_queue_key(hub_id), order_id)
            await self._redis.zrem(WAKEUPS_KEY, wakeup_member(hub_id, order_id))

    async def get_all(self, hub_id: str) -> list[HeldOrder]:
        async with timed_operation("holdqueue.get_all"):
            raw = await self._redis.hgetall(_queue_key(hub_id))
        return [_deserialize(as_text(v)) for v in raw.values()]

    async def depth(self, hub_id: str) -> int:
        """How many orders are waiting, without reading them.

        HLEN rather than len(get_all()): the health check
        (app/health/checks.py) only needs the count, and it runs on a timer
        against every active hub, so deserializing every held order to
        discard it would make a monitoring probe the heaviest reader of
        this queue.
        """
        async with timed_operation("holdqueue.depth"):
            return int(await self._redis.hlen(_queue_key(hub_id)))
