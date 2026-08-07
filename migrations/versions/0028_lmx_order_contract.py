"""lmx order contract

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-07

The LMX Order Object v1 (docs/LMX_LINK_PLAN.md §1.2) persisted onto `orders`,
plus the signup funnel on `clients`. See app/schemas/lmx_order.py for the
field-by-field reasoning and app/orders/state_machine.py for the status machine.

Three things here deserve attention:

1. `orders.client_id` and `orders.shop_id` become NULLABLE. A path with no
   client relationship has no client_id, and an order captured before its
   ad-hoc pickup address has been resolved to a Shop has no shop_id yet.
   Client-scoped queries must filter explicitly; both existing ones
   (app/api/client_routes.py, app/billing/service.py) already do.

2. Four values are ADDED to the `order_status` enum, not replacing it.
   `delivery_failed` and `returned` already carry §1.4's EXCEPTION_RAISED and
   RETURNED_TO_HUB meanings, so adding duplicates would make every status query
   ambiguous forever.

3. `clients.signup_status` backfills to 'active', because every client that
   exists today was onboarded by LMX directly rather than self-signing up.

DOWNGRADE CAVEAT: Postgres cannot remove a value from an enum type. The
downgrade below drops the columns and re-imposes the NOT NULLs, but the four
added status values remain on the type. That is harmless (nothing references
them once the code is rolled back) and the alternative - recreating the type and
rewriting every dependent column - is far riskier than the thing it undoes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_STATUSES = ("accepted", "en_route_pickup", "picked_up", "en_route_drop")


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # Postgres, and Alembic wraps migrations in one. IF NOT EXISTS keeps this
    # idempotent if a partial run is retried.
    for value in _NEW_STATUSES:
        op.execute(f"ALTER TYPE order_status ADD VALUE IF NOT EXISTS '{value}'")

    # --- orders: relax the two FKs -------------------------------------
    op.alter_column("orders", "client_id", existing_type=postgresql.UUID(), nullable=True)
    op.alter_column("orders", "shop_id", existing_type=postgresql.UUID(), nullable=True)

    # --- orders: commitment --------------------------------------------
    op.add_column(
        "orders",
        sa.Column("sla_owner", sa.String(16), nullable=False, server_default="LMX"),
    )
    op.add_column("orders", sa.Column("source_order_ref", sa.String(120), nullable=True))
    op.add_column("orders", sa.Column("promised_at", sa.DateTime(timezone=True), nullable=True))

    # --- orders: ad-hoc origin -----------------------------------------
    op.add_column("orders", sa.Column("pickup_address", sa.String(255), nullable=True))
    op.add_column("orders", sa.Column("pickup_contact_name", sa.String(120), nullable=True))
    op.add_column("orders", sa.Column("pickup_contact_phone", sa.String(32), nullable=True))
    op.add_column("orders", sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True))

    # --- orders: windows -----------------------------------------------
    for column in (
        "pickup_window_start",
        "pickup_window_end",
        "delivery_window_start",
        "delivery_window_end",
    ):
        op.add_column("orders", sa.Column(column, sa.DateTime(timezone=True), nullable=True))

    # --- orders: assignment, proof, economics, modality ----------------
    op.add_column(
        "orders",
        sa.Column("assignment_scope", sa.String(24), nullable=False, server_default="any_driver"),
    )
    op.add_column(
        "orders",
        sa.Column("proof_requirements", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "orders",
        sa.Column("revenue_basis", sa.String(16), nullable=False, server_default="per_drop"),
    )
    op.add_column("orders", sa.Column("quoted_amount_cents", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("cost_actuals_cents", sa.Integer(), nullable=True))
    op.add_column(
        "orders",
        sa.Column("payer_type", sa.String(24), nullable=False, server_default="contract_client"),
    )
    op.add_column(
        "orders",
        sa.Column("payment_status", sa.String(24), nullable=False, server_default="unbilled"),
    )
    op.add_column(
        "orders",
        sa.Column("modality_eligible", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column("orders", sa.Column("modality_assigned", sa.String(32), nullable=True))

    # Idempotent intake for every adapter, for free: a source can't create the
    # same order twice. Partial so the many pre-contract rows with a null
    # source_order_ref don't all collide with each other.
    op.create_index(
        "uq_orders_source_ref",
        "orders",
        ["source_system", "source_order_ref"],
        unique=True,
        postgresql_where=sa.text("source_order_ref IS NOT NULL"),
    )

    # --- clients: the signup funnel ------------------------------------
    op.add_column(
        "clients",
        sa.Column("signup_status", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column("clients", sa.Column("terms_accepted_version", sa.String(32), nullable=True))
    op.add_column(
        "clients", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_clients_signup_status", "clients", ["signup_status"])


def downgrade() -> None:
    op.drop_index("ix_clients_signup_status", table_name="clients")
    op.drop_column("clients", "terms_accepted_at")
    op.drop_column("clients", "terms_accepted_version")
    op.drop_column("clients", "signup_status")

    op.drop_index("uq_orders_source_ref", table_name="orders")

    for column in (
        "modality_assigned",
        "modality_eligible",
        "payment_status",
        "payer_type",
        "cost_actuals_cents",
        "quoted_amount_cents",
        "revenue_basis",
        "proof_requirements",
        "assignment_scope",
        "delivery_window_end",
        "delivery_window_start",
        "pickup_window_end",
        "pickup_window_start",
        "ready_at",
        "pickup_contact_phone",
        "pickup_contact_name",
        "pickup_address",
        "promised_at",
        "source_order_ref",
        "sla_owner",
    ):
        op.drop_column("orders", column)

    # Any row relying on a null client_id/shop_id has to go before the NOT NULLs
    # come back, otherwise the ALTERs fail. These are orders that could only
    # have been created by the LMX Link path being rolled back.
    op.execute("DELETE FROM orders WHERE client_id IS NULL OR shop_id IS NULL")
    op.alter_column("orders", "shop_id", existing_type=postgresql.UUID(), nullable=False)
    op.alter_column("orders", "client_id", existing_type=postgresql.UUID(), nullable=False)

    # The four added order_status values stay on the type - Postgres cannot
    # remove an enum value, and recreating the type would mean rewriting every
    # dependent column. Harmless: nothing references them after a rollback.
