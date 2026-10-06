"""Layer 2 of the golden record: one door, bay or counter a delivery can use.

`docs/ROADMAP_1.5.md` DE-1, decision D-GR. A site (a `Location`) has one or more
handoff points, and **the handoff point, not the site, is the row the modality
model learns from**: "can a car deliver here" is a question about one door - the
counter at the front, the roll-up bay round the back - not about the address.

Until this table the closest thing was the dock survey (`DRV-7`), which writes
access answers onto `receiver_profiles`: one row per dock, from a driver's short
form. That stays the per-dock summary. This is the technician's per-door record,
with the geometry (pins, distances) the short form never asks for.

**Unknown is null, and it is not "no".** A field nobody captured stays null; a
captured "nothing in the way" is an empty `obstructions` list, and a captured
"no open ground" is `drone_open_ground = false`. The record tier
(`record_tiers`, migration 0069) counts a handoff point as captured only when
every field the definitions mark "needed for gold" is non-null - so a guess must
never be written as a value to make a record look complete.

Nothing writes this table yet. The writers are DE-3 (the Experiment 0 import and
the re-drive CSV) and DE-4 (golden capture in the driver app).
"""
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# The definitions doc's list, as codes.
HANDOFF_TYPES = (
    "counter",
    "roll_up_bay",
    "loading_dock",
    "side_door",
    "job_site_trailer",
    "mailroom",
)
# Where the vehicle stopped. Distinct from the dock survey's `stop_point`
# vocabulary on purpose: that one is a driver's quick answer about the dock; this
# is the stop type at one door, which the definitions list separately.
STOP_TYPES = ("curb", "lot", "alley", "loading_zone", "dock_apron")
STEPS_OR_RAMPS = ("none", "steps", "ramp", "steps_and_ramp")
OBSTRUCTION_KINDS = ("gate", "fence", "code_or_buzzer", "parked_vehicles")
OVERHEAD_KINDS = ("wires", "canopy", "trees")


class HandoffPoint(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "handoff_points"

    # The site (Layer 1). A `Location` is the identity layer's physical dock.
    location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("locations.id"), nullable=False, index=True
    )
    handoff_type: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # Pins from the app, never typed. The door is where the item changes hands;
    # the stop point is where the vehicle actually stopped.
    door_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    door_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # False when a sign forbids stopping there.
    stop_legal: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Stop to door. The app measures the distance; a technician counts the steps.
    walk_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    steps_or_ramps: Mapped[str | None] = mapped_column(String(16), nullable=True)
    continuous_sidewalk: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # A list of OBSTRUCTION_KINDS; [] means "checked, nothing in the way".
    obstructions: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    # The drone view: open flat ground near the door, and anything overhead.
    drone_open_ground: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    drone_ground_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    # A list of OVERHEAD_KINDS; [] means "checked, clear overhead".
    overhead: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    # Nice to have, not needed for gold.
    door_width_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dock_height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The four-photo set: object-store keys, time and location stamped by the app,
    # no faces or plates.
    photo_approach: Mapped[str | None] = mapped_column(String(500), nullable=True)
    photo_stop_point: Mapped[str | None] = mapped_column(String(500), nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    photo_handoff: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Who captured it, from where, when. Every record carries its source.
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    collected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    collected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
