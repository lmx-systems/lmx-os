"""calls

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-23

Masked voice calling (docs/ROADMAP.md A7) - app/models/call.py's log of
each "Call" button tap: which stop/driver it was for, the real customer
number it bridges to, and its Twilio lifecycle status.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("stop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stops.id"), nullable=False),
        sa.Column("counterparty_phone", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="initiated"),
        sa.Column("twilio_call_sid", sa.String(64), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
    )
    op.create_index("ix_calls_stop_id", "calls", ["stop_id"])


def downgrade() -> None:
    op.drop_index("ix_calls_stop_id", table_name="calls")
    op.drop_table("calls")
