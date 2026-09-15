"""
Verifies the hand-written migration (migrations/versions/0001_initial_schema.py)
actually runs cleanly against a real Postgres - never exercised before this.
"""
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

# Not marked with pytest.mark.asyncio here - asyncio_mode="auto" (pyproject.toml)
# already handles the async tests below, and this file also has one plain sync
# test (the downgrade/upgrade round trip), which an asyncio mark would wrongly
# apply to.
pytestmark = [pytest.mark.integration]

EXPECTED_TABLES = {
    "hubs",
    "clients",
    "shop_profiles",
    "drivers",
    "routes",
    "orders",
    "stops",
    "stop_flags",
    "proposed_rules",
    "active_rules",
    "alembic_version",
}


async def test_upgrade_head_creates_expected_tables(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
        actual_tables = {row[0] for row in result.all()}
    assert EXPECTED_TABLES.issubset(actual_tables)


async def test_upgrade_head_creates_expected_enums(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT t.typname, e.enumlabel FROM pg_type t "
                "JOIN pg_enum e ON t.oid = e.enumtypid "
                "WHERE t.typname IN ('sla_tier', 'order_status') "
                "ORDER BY t.typname, e.enumsortorder"
            )
        )
        rows = result.all()

    sla_labels = [label for typname, label in rows if typname == "sla_tier"]
    order_status_labels = [label for typname, label in rows if typname == "order_status"]

    assert sla_labels == ["T1", "T2", "T3", "HOT_SHOT"]
    # The last four are the LMX Link contract's stop-level progress states
    # (migration 0028, docs/LMX_LINK_PLAN.md §1.4). They sort at the end rather
    # than in lifecycle order because `ALTER TYPE ... ADD VALUE` appends - this
    # list is enum *storage* order, not the state machine's order, which lives
    # in app/orders/state_machine.py. Don't "tidy" them into sequence; the
    # sort position is a fact about Postgres, not a choice.
    assert order_status_labels == [
        "received",
        "classified",
        "held",
        "queued",
        "assigned",
        "delivered",
        "cancelled",
        "delivery_failed",
        "returned",
        "accepted",
        "en_route_pickup",
        "picked_up",
        "en_route_drop",
    ]


async def test_orders_table_has_expected_foreign_keys(db_engine):
    """Spot-check that FK relationships from Section 10 actually got created."""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT
                    kcu.column_name,
                    ccu.table_name AS referenced_table
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = 'orders'
                """
            )
        )
        fks = {row[0]: row[1] for row in result.all()}

    assert fks["hub_id"] == "hubs"
    assert fks["client_id"] == "clients"
    assert fks["shop_id"] == "shop_profiles"


def test_downgrade_then_upgrade_round_trips_cleanly(_migration_applied):
    """
    Exercises the hand-written downgrade() function, which nothing has
    ever called before this test existed. Runs last in this file
    (alphabetically last collected in tests/integration/) so a failure
    here doesn't leave other integration tests running against a
    half-migrated schema - see the module docstring in conftest.py.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    alembic_cfg = Config(os.path.join(repo_root, "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(repo_root, "migrations"))

    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    # If either step raised, pytest fails this test - no further assertion
    # needed beyond "both directions ran without error."


def test_0046_backfills_one_dock_per_distinct_shop_address(_migration_applied):
    """IDN-1's backfill, run against real rows rather than an empty schema.

    The rest of the suite builds its schema from nothing, so `_backfill()` never
    executes with anything to back-fill - which is precisely the code path that
    touches live customer data. This downgrades to 0045, plants the address
    shapes the design partner's export actually contains, upgrades, and checks
    what came out.

    Sync for the same reason the round-trip test above is: alembic's env.py runs
    `asyncio.run()`, which cannot be called from inside a running event loop. The
    database work therefore goes through `asyncio.run()` on its own engine rather
    than the `db_engine` fixture. Leaves the schema at head and removes the rows
    it planted.
    """
    import asyncio
    import uuid

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import settings

    alembic_cfg = _alembic_config_for_backfill()

    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    # Three spellings of one dock, a second real dock, and the export's `N/A`.
    addresses = [
        "1200 E 6th St, Austin, TX",
        "  1200   E 6th St,  Austin, TX  ",
        "1200 E 6TH ST, AUSTIN, TX",
        "500 Congress Ave, Austin, TX",
        "N/A",
    ]

    async def _run(fn):
        engine = create_async_engine(settings.database_url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    async def _plant(engine):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO hubs (id, name, lat, lng) "
                    "VALUES (:id, 'Backfill Hub', 34.05, -118.25)"
                ),
                {"id": hub_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO clients (id, hub_id, name, pos_system) "
                    "VALUES (:id, :hub, 'Backfill Client', 'flat_file')"
                ),
                {"id": client_id, "hub": hub_id},
            )
            for index, address in enumerate(addresses):
                await conn.execute(
                    text(
                        "INSERT INTO shop_profiles (id, client_id, name, address, lat, lng) "
                        "VALUES (:id, :client, :name, :address, 30.26, -97.73)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "client": client_id,
                        "name": f"Shop {index}",
                        "address": address,
                    },
                )

    async def _read(engine):
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT address, location_id FROM shop_profiles "
                        "WHERE client_id = :client"
                    ),
                    {"client": client_id},
                )
            ).all()
            by_address = {row.address: row.location_id for row in rows}
            display = await conn.scalar(
                text("SELECT address FROM locations WHERE id = :id"),
                {"id": by_address["1200 E 6th St, Austin, TX"]},
            )
            return by_address, display

    async def _cleanup(engine):
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM shop_profiles WHERE client_id = :client"),
                {"client": client_id},
            )
            await conn.execute(text("DELETE FROM locations"))
            await conn.execute(text("DELETE FROM clients WHERE id = :id"), {"id": client_id})
            await conn.execute(text("DELETE FROM hubs WHERE id = :id"), {"id": hub_id})

    command.downgrade(alembic_cfg, "0045")
    try:
        asyncio.run(_run(_plant))
        command.upgrade(alembic_cfg, "head")
        by_address, display = asyncio.run(_run(_read))
    finally:
        command.upgrade(alembic_cfg, "head")
        asyncio.run(_run(_cleanup))

    docks = {loc for loc in by_address.values() if loc is not None}
    # Three spellings collapsed, one separate dock -> two.
    assert len(docks) == 2, f"expected 2 docks, got {len(docks)}"

    assert (
        by_address["1200 E 6th St, Austin, TX"]
        == by_address["  1200   E 6th St,  Austin, TX  "]
        == by_address["1200 E 6TH ST, AUSTIN, TX"]
    )
    assert (
        by_address["500 Congress Ave, Austin, TX"]
        != by_address["1200 E 6th St, Austin, TX"]
    )
    # `N/A` names no place, so it gets no dock rather than an invented one.
    assert by_address["N/A"] is None
    # The dock kept the first spelling seen, not the last.
    assert display == "1200 E 6th St, Austin, TX"


def _alembic_config_for_backfill() -> Config:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(repo_root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(repo_root, "migrations"))
    return cfg
