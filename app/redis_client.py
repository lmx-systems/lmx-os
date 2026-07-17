"""
Redis connection pool + typed helpers for the hot path.

Design doc requirement (Section 7): fleet/route state "must be read in
under 50ms on every re-optimization pass." We keep a single dedicated
connection pool, use pipelining for multi-key reads, and never round-trip
to Postgres inside the optimizer loop.
"""
from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as redis
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

_pool: redis.ConnectionPool | None = None

# Soft budget for a single Redis operation on the hot path. We don't hard-fail
# on this, but we log loudly so a regression is visible before it eats the
# optimizer's 5-second cycle budget.
SLOW_READ_THRESHOLD_MS = 50


def get_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=50,
            decode_responses=True,
        )
    return _pool


def get_client() -> redis.Redis:
    return redis.Redis(connection_pool=get_pool())


@asynccontextmanager
async def timed_operation(op_name: str) -> AsyncGenerator[None, None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms > SLOW_READ_THRESHOLD_MS:
            logger.warning(
                "redis_slow_operation",
                operation=op_name,
                elapsed_ms=round(elapsed_ms, 2),
                threshold_ms=SLOW_READ_THRESHOLD_MS,
            )


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.disconnect()
        _pool = None
