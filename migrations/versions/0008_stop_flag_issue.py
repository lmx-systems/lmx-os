"""stop flag issue

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-20

Adds the driver-facing "flag an issue" feature (wireframe screen of the
same name): three new columns on Stop (failure_reason, flag_note,
flagged_at) and a new 'delivery_failed' Order status. Not the same thing
as the existing StopFlag table, which is an unrelated ops route-planning
annotation for the Learning Loop.

Note on ADD VALUE: same pattern as migration 0007 - this only adds the new
enum value, never uses it in the same transaction, so it's safe to run
inside Alembic's normal transactional DDL on Postgres 12+ (no
autocommit_block needed).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stops", sa.Column("failure_reason", sa.String(32), nullable=True))
    op.add_column("stops", sa.Column("flag_note", sa.String(500), nullable=True))
    op.add_column("stops", sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'delivery_failed'")


def downgrade() -> None:
    """
    Drops the three new Stop columns. Postgres has no `ALTER TYPE ... DROP
    VALUE` - 'delivery_failed' stays in the order_status enum on downgrade,
    same accepted no-op as migration 0007's HOT_SHOT downgrade note.
    """
    op.drop_column("stops", "flagged_at")
    op.drop_column("stops", "flag_note")
    op.drop_column("stops", "failure_reason")
