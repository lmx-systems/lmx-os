"""driver_sign_in_codes - sign-in codes issued from the ops console

Replaces the texted sign-in code, and with it the need for an SMS provider to
sign a driver in. See app/models/driver_sign_in_code.py.

Revision ID: 0071
Revises: 0070
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071"
down_revision: Union[str, None] = "0070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "driver_sign_in_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("code_hmac", sa.String(64), nullable=False),
        sa.Column(
            "issued_by_ops_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ops_users.id"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_device_id", sa.String(128), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code_hmac", name="uq_driver_sign_in_codes_code_hmac"),
    )
    op.create_index("ix_driver_sign_in_codes_driver_id", "driver_sign_in_codes", ["driver_id"])


def downgrade() -> None:
    op.drop_index("ix_driver_sign_in_codes_driver_id", table_name="driver_sign_in_codes")
    op.drop_table("driver_sign_in_codes")
