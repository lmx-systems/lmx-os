"""driver location pings

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-03

Durable driver location trail (docs/ROADMAP.md F1). The Redis fleet-state
hash already holds each driver's *current* position for the optimizer's hot
path, but it overwrites on every ping - so distance actually travelled is
unrecoverable. Miles per drop is one of the nine shadow-mode cutover
scorecard metrics (W9) and needs the path, not the latest point.

Append-only. See app/models/driver_location_ping.py for why there's no
uniqueness constraint on (driver_id, recorded_at) and why retention is
deliberately left as an open decision rather than assumed here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "driver_location_pings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        # Device observation time, not write time - the offline outbox can
        # flush a ping long after the fact, so ordering by created_at would
        # scramble a trail that crossed a dead zone.
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
    )
    op.create_index("ix_driver_location_pings_hub_id", "driver_location_pings", ["hub_id"])
    op.create_index(
        "ix_driver_location_pings_driver_recorded",
        "driver_location_pings",
        ["driver_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_driver_location_pings_driver_recorded", table_name="driver_location_pings")
    op.drop_index("ix_driver_location_pings_hub_id", table_name="driver_location_pings")
    op.drop_table("driver_location_pings")
