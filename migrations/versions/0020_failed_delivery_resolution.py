"""failed delivery resolution

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-27

Failed-delivery / redelivery workflow (docs/ROADMAP.md R5). Two additions:
- orders.delivery_attempts: how many times delivery has been attempted
  (1 = original dispatch), incremented when a failed order is redelivered.
- a `returned` value on the order_status enum, for a failed order resolved
  by sending the parts back to the shop rather than reattempting.

Enum note (same as migration 0007's HOT_SHOT addition): Postgres 16
allows `ALTER TYPE ... ADD VALUE` inside the migration transaction as long
as the new value isn't *used* in that same transaction - it isn't here,
so no autocommit handling is needed. There is no `ALTER TYPE ... DROP
VALUE`, so the downgrade drops the column but deliberately leaves the enum
value in place (documented below).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("orders", sa.Column("failure_reason", sa.String(32), nullable=True))
    op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'returned'")


def downgrade() -> None:
    op.drop_column("orders", "failure_reason")
    op.drop_column("orders", "delivery_attempts")
    # The 'returned' enum value is intentionally left in place - Postgres
    # has no ALTER TYPE ... DROP VALUE, and rebuilding the enum would fail
    # loudly if any order already uses it. An unused extra enum value is
    # harmless; same call migration 0007 made for HOT_SHOT.
