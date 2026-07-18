"""add orders.assigned_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18

Supports DispatchOptimizerService.run_cycle writing back Order.status ->
"assigned" the moment a dispatch actually happens, instead of Order.status
getting stuck on "held" forever once the Redis hold queue has moved on
(docs/ARCHITECTURE.md's "known limitation" this closes).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "assigned_at")
