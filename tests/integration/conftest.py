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
from dataclasses import replace
from datetime import date

import asyncpg
import pytest
import redis.asyncio as redis_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db as db_module
import app.legal.documents as documents
import app.redis_client as redis_client_module
from app.config import settings
from app.db import Base

# Registers every table on Base.metadata, which the truncate below walks. Three
# models were missing from this package's imports until a FK to ops_users made
# the gap visible - see app/models/__init__.py.
import app.models  # noqa: F401

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
def _skip_if_services_unavailable(request: pytest.FixtureRequest) -> None:
    reason = _check_services_available()
    if not reason:
        return

    message = (
        f"Skipping integration tests - {reason}. Start a real Postgres + Redis "
        "and point DATABASE_URL/REDIS_URL at them to run this suite."
    )

    # Recorded on the config object - not imported across conftest modules, since
    # `tests/` is not a package - so tests/conftest.py's pytest_terminal_summary can
    # say, loudly and at the end, that this suite did not run. A skip is otherwise
    # indistinguishable from a pass at a glance, and identical in the exit code.
    request.config.lmx_integration_skip_reason = message  # type: ignore[attr-defined]

    # Opt-in hard failure, for anyone who would rather not have the choice: a
    # local pre-push hook, or a second line of defence in CI beside the grep in
    # .github/workflows/ci.yml. Off by default, because skipping when the services
    # genuinely are not there is the deliberate behaviour this file was built with.
    if os.environ.get("LMX_REQUIRE_INTEGRATION"):
        pytest.fail(
            f"{message} LMX_REQUIRE_INTEGRATION is set, so this is a failure "
            "rather than a skip.",
            pytrace=False,
        )

    pytest.skip(message)


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


@pytest.fixture(autouse=True)
async def _reset_global_engine_pool():
    """
    app/db.py's module-level `engine` (what session_scope() uses - e.g.
    DispatchOptimizerService.run_cycle, called by more than one test in
    tests/integration/test_driver_app_integration.py) is a singleton
    created once at import time, bound to whichever event loop happened to
    be running then. pytest-asyncio hands each test function its own event
    loop, so the second test in a file to touch session_scope() reuses a
    pooled asyncpg connection from a *different* loop and fails with
    "attached to a different loop" - the same class of bug real_redis_client
    above works around for Redis. Disposing the pool before each test forces
    a fresh connection bound to that test's loop on next use.
    """
    await db_module.engine.dispose()
    yield
    await db_module.engine.dispose()


@pytest.fixture
def published_terms(monkeypatch):
    """Run a test against published legal documents.

    `POST /public/signup` refuses to take an application while either document is
    still `status: draft` in app/legal/content/, because a signup writes
    `clients.terms_accepted_version` and a version of an unapproved document records
    assent to nothing. Every test that signs somebody up therefore has to say which
    world it is in.

    This fixture publishes them rather than setting `allow_unpublished_terms`, on
    purpose: the escape hatch is a demo affordance and the published path is the one
    that will actually run in production, so that is the one the flow tests should be
    exercising. The tests for the closed door are in test_legal_documents.py and set
    the flag themselves.
    """
    for name in ("terms", "privacy"):
        published = replace(
            documents.DOCUMENTS[name], status="published", effective=date(2026, 8, 11)
        )
        monkeypatch.setitem(documents.DOCUMENTS, name, published)
        # `documents_are_published()` reads the module-level names, and
        # `current_terms_version()` reads TERMS - both have to move with the dict or
        # the fixture would publish only half of what the code consults.
        monkeypatch.setattr(documents, name.upper(), published)
    return documents.DOCUMENTS


async def make_driver_compliant(db_session, driver_id, *, expires_in_days: int = 180):
    """Give a driver the verified documents they now need to go on shift (R4).

    Exists because "go online" stopped being free. Every required document must be
    present, uploaded, reviewed by an ops user, and unexpired per the reviewer's
    date - so any test that puts a driver on the road has to say so explicitly
    rather than inheriting a gate that passed by default.

    That the default USED to be compliant is precisely the bug
    (app/compliance/driver_documents.py): the old gate only refused when a document
    row on file had passed a driver-typed expiry, so a driver with no documents at
    all sailed through.
    """
    from datetime import date, datetime, timedelta, timezone

    from app.models.driver_document import REQUIRED_DOC_TYPES, REVIEW_VERIFIED, DriverDocument

    expiry = date.today() + timedelta(days=expires_in_days)
    for doc_type in REQUIRED_DOC_TYPES:
        db_session.add(
            DriverDocument(
                driver_id=driver_id,
                doc_type=doc_type,
                claimed_expires_at=expiry,
                verified_expires_at=expiry,
                review_status=REVIEW_VERIFIED,
                reviewed_at=datetime.now(timezone.utc),
                file_url=f"local-capture://driver-documents/{driver_id}/{doc_type}/test",
            )
        )
    await db_session.commit()
