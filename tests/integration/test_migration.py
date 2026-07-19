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
    assert order_status_labels == [
        "received",
        "classified",
        "held",
        "queued",
        "assigned",
        "delivered",
        "cancelled",
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
