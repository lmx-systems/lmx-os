"""parcels

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-28

Package identity (docs/ROADMAP.md W10) - a parcels table giving each
physical package a unique, scannable identity within a hub, so scan-at-
pickup can verify it against the expected order and catch WRONG_PART before
the driver leaves. barcode is ownership-agnostic (an LMX-generated code or
the distributor's own pick-ticket barcode).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "parcels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("barcode", sa.String(128), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("hub_id", "barcode", name="uq_parcel_hub_barcode"),
    )
    op.create_index("ix_parcels_hub_id", "parcels", ["hub_id"])
    op.create_index("ix_parcels_order_id", "parcels", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_parcels_order_id", table_name="parcels")
    op.drop_index("ix_parcels_hub_id", table_name="parcels")
    op.drop_table("parcels")
