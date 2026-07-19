"""driver messaging (phase 3)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-19

Screens 1p/1q: masked SMS contact with the customer (per dropoff) and
with LMX dispatch/support. See app/models/message.py and
app/messaging/sms_client.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("stop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stops.id"), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("counterparty_phone", sa.String(32), nullable=True),
        sa.Column("twilio_sid", sa.String(64), nullable=True),
    )
    op.create_index("ix_messages_driver_id", "messages", ["driver_id"])
    op.create_index("ix_messages_stop_id", "messages", ["stop_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_stop_id", table_name="messages")
    op.drop_index("ix_messages_driver_id", table_name="messages")
    op.drop_table("messages")
