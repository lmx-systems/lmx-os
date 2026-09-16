"""stop_geofence_events: machine-recorded arrive/depart, beside the driver's taps

DRV-1 (docs/ROADMAP_1.5.md Phase 1). The sensor the whole phase is built around.

`stops.arrived_at` is when a driver tapped a button, stamped by the server.
These rows are when the phone crossed the stop's boundary, stamped by the phone.
Both are kept. Overwriting the first with the second would destroy the only
comparison that shows whether the sensor worked - and that comparison is the
claim: the design partner's export is minute-resolution with 65.5% of stops
computing to zero dwell, while the second-precision file shows those same stops
really took 3-50 seconds.

`occurred_at` (device) and `recorded_at` (server) are both stored. DRV-4's
outbox holds events through a dead zone and flushes on reconnect, so a whole
afternoon can arrive at once; a server-stamped time would collapse the shift
onto the reconnect instant, which is the failure the outbox exists to prevent.
Keeping both makes clock skew measurable rather than invisible.

UNIQUE on (stop_id, kind, occurred_at) because the outbox retries. A replayed
crossing has to be a conflict the database refuses, not a second arrival that
doubles a dwell sample.

No backfill - there is nothing to back-fill. Every row here comes from a phone
that has crossed a boundary, and none has yet.

Revision ID: 0050
Revises: 0049
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stop_geofence_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "stop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stops.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
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
        sa.UniqueConstraint(
            "stop_id", "kind", "occurred_at", name="uq_stop_geofence_event_crossing"
        ),
        sa.CheckConstraint("kind IN ('enter', 'exit')", name="ck_stop_geofence_event_kind"),
        sa.CheckConstraint(
            "accuracy_m IS NULL OR accuracy_m >= 0", name="ck_stop_geofence_event_accuracy"
        ),
    )
    # The hot read is "every crossing at this stop", for dwell.
    op.create_index("ix_stop_geofence_events_stop_id", "stop_geofence_events", ["stop_id"])


def downgrade() -> None:
    """Drops the sensor's output.

    Not recoverable: these rows are observations of physical events that have
    already happened. Unlike the dwell aggregates in receiver_profiles, which
    recompute, nothing can regenerate a crossing.
    """
    op.drop_index("ix_stop_geofence_events_stop_id", table_name="stop_geofence_events")
    op.drop_table("stop_geofence_events")
