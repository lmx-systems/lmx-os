"""return item standalone (nullable origin order)

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-30

Returns & core pickups slice 2 (docs/ROADMAP.md W1): make
return_items.origin_order_id nullable so a shop can flag *standalone*
accumulated cores for pickup - returns not tied to a single originating
delivery order.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("return_items", "origin_order_id", nullable=True)


def downgrade() -> None:
    # Reverting to NOT NULL would fail if any standalone return exists; that's
    # correct - those rows have no origin order to backfill.
    op.alter_column("return_items", "origin_order_id", nullable=False)
