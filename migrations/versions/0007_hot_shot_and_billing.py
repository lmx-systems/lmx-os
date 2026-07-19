"""hot shot tier, client billing rates, client portal auth, order fee

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-19

Phase 8 (see docs/ROADMAP.md): a new HOT_SHOT delivery tier (direct,
never commingled with another order's pickup - see accept_offer in
app/api/driver_routes.py), a per-client per-tier billing rate
(client_rates), a client-portal login (Client.portal_email/
portal_password_hash), and a real per-order fee (Order.fee_cents).

Note on HOT_SHOT and the sla_tier enum: `ALTER TYPE ... ADD VALUE` can
run inside a transaction on Postgres 12+ (this project targets
postgres:16-alpine - see docker-compose.yml) as long as the new value
isn't *used* in the same transaction it's added in. This migration only
adds it, so no special autocommit handling is needed here. Downgrading
this specific piece is intentionally not attempted - Postgres has no
`ALTER TYPE ... DROP VALUE`, and rebuilding the enum without HOT_SHOT
would fail outright (loudly, which is correct) if any Order already
carries that tier. See downgrade()'s docstring below.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE sla_tier ADD VALUE IF NOT EXISTS 'HOT_SHOT'")

    op.create_table(
        "client_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("sla_tier", sa.String(16), nullable=False),
        sa.Column("rate_per_drop_cents", sa.Integer(), nullable=False),
        sa.UniqueConstraint("client_id", "sla_tier", name="uq_client_rates_client_tier"),
    )

    op.add_column("clients", sa.Column("portal_email", sa.String(255), nullable=True))
    op.add_column("clients", sa.Column("portal_password_hash", sa.String(255), nullable=True))
    op.create_unique_constraint("uq_clients_portal_email", "clients", ["portal_email"])

    op.add_column("orders", sa.Column("fee_cents", sa.Integer(), nullable=True))


def downgrade() -> None:
    """
    Drops everything this migration added except the HOT_SHOT enum value
    itself - Postgres has no `ALTER TYPE ... DROP VALUE`, and the standard
    workaround (recreate the type without it, cast the column over) would
    fail loudly if any Order row already has sla_tier='HOT_SHOT', which is
    the correct behavior: this migration should not silently destroy real
    data on downgrade. If a true rollback of the enum value itself is ever
    needed, do it by hand after confirming no rows depend on it.
    """
    op.drop_column("orders", "fee_cents")
    op.drop_constraint("uq_clients_portal_email", "clients", type_="unique")
    op.drop_column("clients", "portal_password_hash")
    op.drop_column("clients", "portal_email")
    op.drop_table("client_rates")
