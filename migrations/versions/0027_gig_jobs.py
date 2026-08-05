"""gig jobs

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-04

The gig-platform demand path's store (docs/ROADMAP.md G3). Deliberately a
separate table from orders rather than nullable columns on it: a gig job has
no client, no SLA tier we assigned, and no per-drop fee from a rate table -
the platform sets the windows and the pay.

Sits upstream of every intake path (notification listener, share sheet,
manual entry), which is why it lands before any of them. See
app/models/gig_job.py for the assignment-scope reasoning - a per-job
property rather than a system mode, because both onboarding tracks run
simultaneously during any migration.

Unrelated to the existing gig_payouts table, which is about paying
gig-classified LMX drivers (A11). Same word, opposite direction.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gig_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        # Null until someone accepts - an offer under evaluation has no owner.
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=True),
        sa.Column("source_platform", sa.String(24), nullable=False),
        sa.Column("intake_source", sa.String(24), nullable=False, server_default="manual"),
        sa.Column("platform_job_ref", sa.String(64), nullable=False),
        sa.Column("pickup_address", sa.String(255), nullable=False),
        sa.Column("pickup_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("pickup_lng", sa.Numeric(9, 6), nullable=True),
        # Nullable because a collapsed offer card hides the dropoff address
        # (G2) - enough to reject an offer, not enough to sequence it.
        sa.Column("dropoff_address", sa.String(255), nullable=True),
        sa.Column("dropoff_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("dropoff_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("pickup_window_open", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pickup_window_close", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dropoff_window_open", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dropoff_window_close", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pay_cents", sa.Integer(), nullable=False),
        sa.Column("distance_miles", sa.Numeric(7, 2), nullable=True),
        sa.Column("assignment_scope", sa.String(24), nullable=False, server_default="pinned_to_driver"),
        sa.Column("status", sa.String(24), nullable=False, server_default="offered"),
        # Platform surface time vs. our capture time - the gap is the
        # intake-latency question G1's scope depends on.
        sa.Column("offered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        # Intake paths are expected to overlap; double-capture should be a
        # conflict to resolve, not a duplicate found later in the data.
        sa.UniqueConstraint("source_platform", "platform_job_ref", name="uq_gig_job_platform_ref"),
    )
    op.create_index("ix_gig_jobs_driver_pickup", "gig_jobs", ["driver_id", "pickup_window_open"])
    op.create_index("ix_gig_jobs_hub_offered", "gig_jobs", ["hub_id", "offered_at"])


def downgrade() -> None:
    op.drop_index("ix_gig_jobs_hub_offered", table_name="gig_jobs")
    op.drop_index("ix_gig_jobs_driver_pickup", table_name="gig_jobs")
    op.drop_table("gig_jobs")
