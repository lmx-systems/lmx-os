"""Messages without Twilio: who replied, and no phone-network columns

LMX OS no longer sends a text or places a call. Support is a thread between the
driver app and the ops console, so a message records the dispatcher who wrote a
reply rather than a phone number and a Twilio message id. Masked calls are gone,
and their log with them.

Nothing has been deployed, so no production rows are lost. The downgrade
recreates the columns and the table empty.

Revision ID: 0072
Revises: 0071
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0072"
down_revision: Union[str, None] = "0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("ops_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ops_users.id"), nullable=True),
    )
    op.drop_column("messages", "twilio_sid")
    op.drop_column("messages", "counterparty_phone")
    op.drop_table("calls")


def downgrade() -> None:
    op.create_table(
        "calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("stop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stops.id"), nullable=False),
        sa.Column("counterparty_phone", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="initiated"),
        sa.Column("twilio_call_sid", sa.String(64), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_calls_stop_id", "calls", ["stop_id"])
    op.add_column("messages", sa.Column("counterparty_phone", sa.String(32), nullable=True))
    op.add_column("messages", sa.Column("twilio_sid", sa.String(64), nullable=True))
    op.drop_column("messages", "ops_user_id")
