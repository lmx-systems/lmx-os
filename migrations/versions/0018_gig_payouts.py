"""gig payouts

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-23

Real per-delivery pay model for gig-classified drivers (docs/ROADMAP.md
A11): drivers.stripe_connect_account_id (where a payout would actually be
sent, unset until a self-serve onboarding flow exists to set it) and the
new gig_payouts table (app/models/gig_payout.py's per-completed-stop
payout log, unique(stop_id) as a real idempotency backstop).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("drivers", sa.Column("stripe_connect_account_id", sa.String(64), nullable=True))

    op.create_table(
        "gig_payouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("stop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stops.id"), nullable=False, unique=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("stripe_transfer_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_gig_payouts_driver_id", "gig_payouts", ["driver_id"])


def downgrade() -> None:
    op.drop_index("ix_gig_payouts_driver_id", table_name="gig_payouts")
    op.drop_table("gig_payouts")
    op.drop_column("drivers", "stripe_connect_account_id")
