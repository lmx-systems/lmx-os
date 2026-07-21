"""driver devices

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-20

Device-bound driver auth: a driver_devices row per phone+device combo, so
a JWT's device_id claim can be checked/revoked without invalidating every
device a driver has ever signed in on. Revocation itself is a Redis
denylist (app/driver_auth/dependencies.py), not a query against this
table - this table is the durable record backing the self-service
"which devices am I signed in on" list and the un-revoke-on-re-OTP path.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "driver_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("device_name", sa.String(120), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("driver_id", "device_id", name="uq_driver_devices_driver_device"),
    )


def downgrade() -> None:
    op.drop_table("driver_devices")
