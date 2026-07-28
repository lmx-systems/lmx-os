"""return items

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-28

Returns & core pickups (docs/ROADMAP.md W1), slice 1 - the reverse leg.
A return_items row is a core/return linked to its originating delivery
order, collected on the delivery visit (piggyback) and brought back to the
shop.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "return_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("origin_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shop_profiles.id"), nullable=False),
        sa.Column("manifest", sa.String(500), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="expected"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_return_items_hub_id", "return_items", ["hub_id"])
    op.create_index("ix_return_items_origin_order_id", "return_items", ["origin_order_id"])


def downgrade() -> None:
    op.drop_index("ix_return_items_origin_order_id", table_name="return_items")
    op.drop_index("ix_return_items_hub_id", table_name="return_items")
    op.drop_table("return_items")
