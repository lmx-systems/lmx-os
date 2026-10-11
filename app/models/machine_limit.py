"""What each machine can carry and where it can go, under a named version.

`docs/ROADMAP_1.5.md` DE-2. A verdict on whether a drone, sidewalk robot or car
could have made a delivery is only meaningful against the limits it was scored
with, and those limits are not settled: our own documents put the drone's
payload at 2.5 kg in one place and about 9 lb in another. So the limits are rows,
not constants, and every label names the version it was scored against
(`machine_labels.machine_limits_version`, required since migration 0073).

**A version is three rows, one per machine, and is never edited.** When an
operator confirms a limit, the change is a new version; labels scored under the
old one keep pointing at what was true when they were scored. The labels' foreign
key is on (version, machine), so a drone verdict cannot cite a version that has
no drone row.

**Every row says how it is known** (`basis`): `assumed` (set by hand or from a
planning document), `operator_reported` (an operator told us), or `measured`.
The seed version, `v1`, is assumed throughout.

What the limits mean, each read against a handoff point (`handoff_points`) or
the item (`orders.item_*`):

    max_weight_kg               the item's weight, inclusive
    max_size_class              the largest `SizeClass` the machine takes
    hazmat_or_liquid_allowed    whether a hazardous or liquid item can go
    needs_step_free_path        no steps between the stop and the door
    needs_continuous_sidewalk   a sidewalk all the way to the door
    needs_open_ground           open, flat ground near the door to set down on
    max_walk_distance_m         from the stop to the door; null is no limit
"""
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# How a limit is known. `assumed` is the honest default until an operator says.
BASES = ("assumed", "operator_reported", "measured")


class MachineLimit(Base):
    __tablename__ = "machine_limits"
    __table_args__ = (
        CheckConstraint("machine IN ('drone', 'sidewalk_robot', 'car')", name="ck_machine_limits_machine"),
        CheckConstraint("max_weight_kg > 0", name="ck_machine_limits_weight"),
        CheckConstraint(
            "max_size_class IN ('envelope', 'small', 'medium', 'large', 'oversize')",
            name="ck_machine_limits_size_class",
        ),
        CheckConstraint(
            "max_walk_distance_m IS NULL OR max_walk_distance_m >= 0", name="ck_machine_limits_walk_distance"
        ),
        CheckConstraint("basis IN ('assumed', 'operator_reported', 'measured')", name="ck_machine_limits_basis"),
    )

    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    machine: Mapped[str] = mapped_column(String(16), primary_key=True)
    max_weight_kg: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    max_size_class: Mapped[str] = mapped_column(String(16), nullable=False)
    hazmat_or_liquid_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    needs_step_free_path: Mapped[bool] = mapped_column(Boolean, nullable=False)
    needs_continuous_sidewalk: Mapped[bool] = mapped_column(Boolean, nullable=False)
    needs_open_ground: Mapped[bool] = mapped_column(Boolean, nullable=False)
    max_walk_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    basis: Mapped[str] = mapped_column(String(24), nullable=False)
    # Where the figures came from, in words: a document, an operator, a measurement.
    source: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
