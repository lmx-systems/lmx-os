"""employment type and shift events

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-21

Foundational schema for the phased W2 -> 1099 -> gig driver rollout
(docs/NEXT_STEPS.md): drivers.employment_type so pay model/document set/
onboarding path can branch per driver, drivers.hourly_rate_cents so pay
rate stops being one global placeholder constant, and driver_shift_events
as a durable online/offline/break history (Redis fleet state only ever
holds the current status, not a log) - the raw data a real hours-worked
calculation will read from once that calculation is built.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "drivers",
        sa.Column("employment_type", sa.String(24), nullable=False, server_default="w2"),
    )
    op.add_column("drivers", sa.Column("hourly_rate_cents", sa.Integer(), nullable=True))

    op.create_table(
        "driver_shift_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_driver_shift_events_driver_id_occurred_at",
        "driver_shift_events",
        ["driver_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_driver_shift_events_driver_id_occurred_at", table_name="driver_shift_events")
    op.drop_table("driver_shift_events")
    op.drop_column("drivers", "hourly_rate_cents")
    op.drop_column("drivers", "employment_type")
