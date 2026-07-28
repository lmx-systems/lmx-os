"""hub closures

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-28

Hub closure / holiday calendar (docs/ROADMAP.md R6). A hub_closures row
marks a local calendar day (in the hub's own timezone) that a hub is not
operating, so the optimizer skips dispatch and the Learning Loop's nightly
job skips that day instead of assuming every active hub runs every day.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hub_closures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("closure_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.UniqueConstraint("hub_id", "closure_date", name="uq_hub_closure_hub_date"),
    )
    op.create_index("ix_hub_closures_hub_id", "hub_closures", ["hub_id"])


def downgrade() -> None:
    op.drop_index("ix_hub_closures_hub_id", table_name="hub_closures")
    op.drop_table("hub_closures")
