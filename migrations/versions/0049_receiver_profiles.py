"""receiver_profiles: what we know about each dock, kept by how we know it

IDN-4 (docs/ROADMAP_1.5.md Phase 1). Done when dwell, hours, access and autonomy
flags are queryable per dock.

One row per `locations.id`, enforced UNIQUE - two profiles would be two answers
to "how long does this place take" with nothing to choose between them. It hangs
off the dock and not the shop because §2.2(b) says so and because it is true:
two accounts at one loading bay share its door and its dwell, not its contract.

**The columns are grouped by how the fact was obtained, and that is the point.**
Dwell is *observed* from stops we made. Hours are *stated* by the receiver.
Access and autonomy fit are *surveyed* by a driver at the door. Each group
carries its own timestamp, because a reader needs to know whether a field is
evidence, a claim, or a six-month-old note.

**Dwell is stored in seconds.** A third of stops at the design partner are under
60 seconds; minutes would round most of this dataset to zero, which is the exact
defect that makes their own export unusable (65.5% of stops compute to zero
dwell there). `dwell_censored_count` is separate because a failed stop never
produced a true dwell, only a lower bound (M1b) - averaging those in biases the
worst docks downward, which is the one direction that breaks a promise.

**The autonomy columns land now, thirty weeks before SUP-3 reads them.**
`MODEL_AND_DATA_BRIEF.md` gives that instruction directly: they cost nothing
today and adding them later means sending somebody back to all ~230 doors.

No backfill. Dwell is computed on demand by `refresh_dwell_statistics`, and
every other column needs a person who has been to the dock.

Revision ID: 0049
Revises: 0048
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "receiver_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id"),
            nullable=False,
        ),
        # --- observed -----------------------------------------------------
        sa.Column("dwell_sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dwell_p50_seconds", sa.Integer(), nullable=True),
        sa.Column("dwell_p90_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "dwell_censored_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("dwell_observed_at", sa.DateTime(timezone=True), nullable=True),
        # --- stated -------------------------------------------------------
        sa.Column("receiving_hours", postgresql.JSONB(), nullable=True),
        sa.Column("hours_source", sa.String(length=16), nullable=True),
        sa.Column("hours_stated_at", sa.DateTime(timezone=True), nullable=True),
        # --- surveyed: access ----------------------------------------------
        sa.Column("appointment_required", sa.Boolean(), nullable=True),
        sa.Column("walk_distance_band", sa.String(length=16), nullable=True),
        sa.Column("carry_effort", sa.String(length=16), nullable=True),
        sa.Column("access_notes", sa.String(length=500), nullable=True),
        # --- surveyed: autonomy fit (M5 labels) ------------------------------
        sa.Column("landing_surface", sa.String(length=16), nullable=True),
        sa.Column("curb_access", sa.String(length=16), nullable=True),
        sa.Column("door_path", sa.String(length=16), nullable=True),
        sa.Column("obstruction", sa.String(length=24), nullable=True),
        sa.Column("who_receives", sa.String(length=16), nullable=True),
        sa.Column("surveyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("location_id", name="uq_receiver_profiles_location_id"),
        # A sample count cannot be negative, and a percentile cannot be. Cheap
        # to state where it holds regardless of which code path writes the row.
        sa.CheckConstraint("dwell_sample_count >= 0", name="ck_receiver_profiles_samples"),
        sa.CheckConstraint(
            "dwell_p50_seconds IS NULL OR dwell_p50_seconds >= 0",
            name="ck_receiver_profiles_p50",
        ),
        sa.CheckConstraint(
            "dwell_p90_seconds IS NULL OR dwell_p90_seconds >= 0",
            name="ck_receiver_profiles_p90",
        ),
    )
    # No separate index on location_id: the unique constraint is backed by one,
    # same as locations.normalized_address in 0046.


def downgrade() -> None:
    """Drops every survey answer.

    The dwell columns are recoverable - `refresh_dwell_statistics` recomputes
    them from stops. The surveyed columns are not: they exist only here, and
    getting them back means sending somebody to every door again.
    """
    op.drop_table("receiver_profiles")
