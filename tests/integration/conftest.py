"""
Fixtures for tests that hit a real Postgres + Redis instead of
fakeredis/pure functions (see tests/conftest.py for the offline suite).

These auto-skip with a clear message if a real Postgres/Redis isn't
reachable at DATABASE_URL/REDIS_URL - so `pytest` still runs clean and fast
for anyone who hasn't set up local services, and CI is expected to
provide real service containers (see .github/workflows/ci.yml) rather than
skip these silently.
"""
from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
import redis.asyncio as redis_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.redis_client as redis_client_module
from app.config import settings
from app.db import Base

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _alembic_config() -> Config:
    cfg = Config(os.path.join(REPO_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(REPO_ROOT, "migrations"))
    return cfg


def _check_services_available() -> str | None:
    """Returns None if both are reachable, else a human-readable reason."""

    async def _check() -> str | None:
        try:
            conn = await asyncpg.connect(dsn=settings.database_url.replace("+asyncpg", ""))
            await conn.close()
        except Exception as exc:  # noqa: BLE001 - reporting, not handling
            return f"Postgres not reachable at {settings.database_url}: {exc}"

        try:
            client = redis_asyncio.from_url(settings.redis_url)
            await client.ping()
            await client.aclose()
        except Exception as exc:  # noqa: BLE001
            return f"Redis not reachable at {settings.redis_url}: {exc}"

        return None

    return asyncio.run(_check())


@pytest.fixture(scope="session", autouse=True)
def _skip_if_services_unavailable() -> None:
    reason = _check_services_available()
    if reason:
        pytest.skip(
            f"Skipping integration tests - {reason}. Start a real Postgres + Redis "
            "and point DATABASE_URL/REDIS_URL at them to run this suite."
        )


@pytest.fixture(scope="session")
def _migration_applied() -> bool:
    """
    Drops and recreates the public schema, then runs `alembic upgrade
    head` for real - this is the thing next-steps item 1 flagged as never
    having been exercised against a live database. Session-scoped since
    this is expensive DDL work that only needs to happen once.

    Deliberately does NOT hand out the engine/connection it used - asyncpg
    connections are bound to the event loop they were created in, and
    pytest-asyncio gives each test function its own event loop by default.
    A session-scoped engine handed to function-scoped async tests fails
    with "another operation is in progress" the moment a second test tries
    to use it. See db_engine below for the fixture tests actually use.
    """

    async def _reset_schema() -> None:
        engine = create_async_engine(settings.database_url)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(_reset_schema())
    command.upgrade(_alembic_config(), "head")
    return True


@pytest.fixture
async def db_engine(_migration_applied):
    """Fresh engine per test - safe to use from whatever event loop that test runs in."""
    engine = create_async_engine(settings.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """Function-scoped session; truncates all tables after each test for isolation."""
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
        await session.rollback()

    table_names = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    async with db_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def real_redis_client():
    """
    Raw client for test setup/teardown (flushing). App code under test
    (FleetStateManager, HoldQueueStore, etc.) talks to the same real Redis
    through its own app.redis_client.get_client() - not through this
    fixture - since settings.redis_url already points at it.

    app.redis_client keeps its connection pool as a module-level singleton
    (app.redis_client._pool) - correct for the real app, which is one
    process with one event loop, but pytest-asyncio gives each test
    function its own event loop by default. Reusing that singleton pool
    across tests fails with "attached to a different loop" the moment a
    second test touches Redis through app code - so it's reset before and
    after every test here, not just once per session.
    """
    await redis_client_module.close_pool()
    client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.flushdb()
    await client.aclose()
    await redis_client_module.close_pool()
