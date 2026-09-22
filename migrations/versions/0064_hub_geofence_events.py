"""hub_geofence_events: the warehouse crossing, and turnaround (`DRV-3`)

`DRV-1` measures how long a driver spends at a customer's dock. This measures
how long they spend at ours — the turnaround between coming back off a route
and leaving on the next one, which `ROADMAP_1.5.md` states as the done-when.

It is a real cost input rather than a curiosity: `M4`'s trip cost counts the
driver-hours a route consumes, and the twenty minutes spent reloading in the
yard are as real as the twenty spent driving.

**A separate table from `stop_geofence_events`**, whose `stop_id` is a
non-nullable foreign key. Widening that to carry two subjects would mean every
reader of a dwell sample having to remember to exclude the hub, and the first
one to forget gets a dwell distribution with the depot in it.

Keyed on `(hub_id, driver_id, kind, occurred_at)` because several drivers cross
the same fence within a minute of each other — a stop crossing identifies
itself by its stop, and this one cannot.

Revision ID: 0064
Revises: 0063
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hub_geofence_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column(
            "driver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("drivers.id"), nullable=False
        ),
        sa.Column("kind", sa.String(length=8), nullable=False),
        # The phone's clock. The outbox holds these through a dead zone and
        # flushes on reconnect, so stamping server-side would collapse a whole
        # afternoon onto the moment the signal returned.
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.CheckConstraint("kind IN ('enter', 'exit')", name="ck_hub_geofence_event_kind"),
        # Idempotent by construction: the outbox retries, so the same crossing
        # arrives more than once. A replay is a conflict the database refuses
        # rather than a second arrival that invents a turnaround.
        sa.UniqueConstraint(
            "hub_id", "driver_id", "kind", "occurred_at",
            name="uq_hub_geofence_event_crossing",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hub_geofence_events_hub_id", "hub_geofence_events", ["hub_id"])
    op.create_index("ix_hub_geofence_events_driver_id", "hub_geofence_events", ["driver_id"])
    op.create_index("ix_hub_geofence_events_occurred_at", "hub_geofence_events", ["occurred_at"])


def downgrade() -> None:
    """Drops every recorded warehouse crossing.

    Not recoverable: these are sensor readings from phones, and nothing else
    holds them. Any turnaround figure computed from them loses its basis.
    """
    for index in (
        "ix_hub_geofence_events_occurred_at",
        "ix_hub_geofence_events_driver_id",
        "ix_hub_geofence_events_hub_id",
    ):
        op.drop_index(index, table_name="hub_geofence_events")
    op.drop_table("hub_geofence_events")
