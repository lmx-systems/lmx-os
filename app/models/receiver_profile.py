"""What we know about a dock: how long it takes, when it is open, how you get in.

`docs/ROADMAP_1.5.md` IDN-4. One row per `Location`, never per `Shop` - §2.2(b)
is explicit that the receiver profile hangs off the dock. Two accounts at one
loading bay share its hours, its door and its dwell; they do not share a
contract.

**Three kinds of knowledge, kept apart on purpose.**

  - *Observed* - dwell, computed from stops we actually made. It is evidence.
  - *Stated* - receiving hours, told to us by the receiver. It is a claim, and
    receivers are wrong about their own hours often enough to matter.
  - *Surveyed* - access and autonomy fit, recorded by a driver standing at the
    door.

They are not interchangeable, and a column that mixed them would be unusable
for the one job this table exists to do. Each group carries its own source and
its own timestamp, so a reader can tell what a field is worth.

**The dwell columns are an observation, not a promise.** They are a summary of
what has happened at this dock, and `M1`/`PRD-5` is the model that predicts what
will happen next. Nothing here should be used as an SLA commitment: median dwell
at the design partner is 2.1 minutes with a third of stops under 60 seconds,
against ~13 minutes at a national distributor. `MODEL_AND_DATA_BRIEF.md` §M1 is
blunt that those are not the same operation and a pooled number is wrong about
both. A stored p50 is a fact about a sample, not a prediction, and the sample
size is stored beside it so nobody has to guess how much to trust it.

**Why the autonomy columns exist now, thirty weeks before anything reads them.**
`MODEL_AND_DATA_BRIEF.md`'s dock-survey section gives the instruction directly:
*"Collect the autonomy-fit columns in the first migration... They cost nothing
now and are expensive to backfill - you would have to revisit every dock."* They
are `M5`'s labels and `SUP-3`/`SUP-4`'s input - the weight-and-dock join a drone
operator asked for and could get nowhere. Adding them later means sending
somebody back to all ~230 doors.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# How a fact got here. Same discipline as IDN-3's node_class_source: a human
# statement must never be silently replaced by a machine's guess.
SOURCE_OBSERVED = "observed"
SOURCE_STATED = "stated"
SOURCE_SURVEYED = "surveyed"
# Measured by a previous operator at the same dock. Not `observed`, which
# means we saw it - the distinction is the whole reason the inherited dwell
# columns exist rather than being written into the observed ones.
SOURCE_INHERITED = "inherited"

# --- M5 label vocabularies (MODEL_AND_DATA_BRIEF.md §M5) -------------------
# Small closed sets, stored as strings rather than Postgres enums for the same
# reason node_class is: none of these has been validated against a real survey
# yet, and the first driver to use the form will find a missing value. Adding a
# constant is cheap; adding an enum value is a migration.

LANDING_SURFACES = ("paved_lot", "gravel", "grass", "street_only", "rooftop", "none")
CURB_ACCESS = ("direct", "short_walk", "long_walk", "no_curb")
DOOR_PATHS = ("ground_level", "steps", "ramp", "loading_dock", "freight_lift")
OBSTRUCTIONS = ("none", "gate", "security_desk", "narrow_access", "overhead_limit")
WHO_RECEIVES = ("anyone", "named_person", "counter_staff", "dock_crew", "unattended_ok")

# Where a driver can legally leave the vehicle (`DRV-7`). The one survey answer
# that had no column: every other thing the dock survey asks was already here,
# validated and tested and written by nothing. This is the fact that decides
# whether a stop is servable at all, and `none_legal` is a real answer - a dock
# a van cannot lawfully stop at is exactly what an autonomy programme needs to
# know about, and rounding it to `street_legal` would hide it.
STOP_POINTS = (
    "loading_dock",
    "marked_bay",
    "lot",
    "street_legal",
    "double_parked",
    "none_legal",
)

CARRY_EFFORTS = ("hand_carry", "two_person", "trolley", "forklift")
WALK_DISTANCE_BANDS = ("at_vehicle", "under_20m", "under_100m", "over_100m")


class ReceiverProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "receiver_profiles"

    # Unique: one profile per dock. A second row would mean two answers to "how
    # long does this place take", and nothing could choose between them.
    location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id"), nullable=False, unique=True, index=True
    )

    # --- Observed: dwell -------------------------------------------------
    # Seconds, because a third of stops at the design partner are under 60 of
    # them and minutes would round most of this dataset to zero - which is the
    # exact defect that makes their own export unusable.
    dwell_sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dwell_p50_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dwell_p90_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Stops that were flagged or failed never produced a true dwell - we only
    # know it exceeded some value (M1b, censored dwell). Counted, not averaged
    # in: dropping them silently biases the worst docks downward, which is
    # precisely where an SLA promise would break.
    dwell_censored_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dwell_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Inherited: dwell measured by somebody else (`IDN-4`, migration 0062) ---
    # The cold-start prior. A dock we have never delivered to has no dwell of its
    # own, and the design partner's second-precision export carries real
    # observations for hundreds of the same physical docks.
    #
    # Its own columns, never the ones above. The nightly refresh recomputes those
    # from our stops and would erase an imported figure by 2am; and they are
    # different measurements - different drivers, possibly a different process at
    # the same door. Dwell being mostly a property of the dock is `M1`'s premise,
    # not a licence to blend the two before anyone has checked it.
    #
    # No inherited p90: a tail estimate from somebody else's operation is the
    # number most likely to be quoted and least likely to survive our own drivers.
    inherited_dwell_p50_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inherited_dwell_sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inherited_dwell_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The period the observations cover, not when the import ran. A dwell from
    # two years ago at a dock that has since rebuilt its bay is worth less, and
    # an import date cannot tell you that.
    inherited_dwell_observed_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    inherited_dwell_observed_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Stated: receiving hours -----------------------------------------
    # {"mon": [["08:00","17:00"]], ...} - a list of windows per weekday, because
    # a dock that shuts for lunch is one place with two windows, not two places.
    # JSONB rather than columns: the shape is a claim from a receiver, and we do
    # not yet know how irregular real answers are.
    receiving_hours: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    hours_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    hours_stated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Surveyed: access -------------------------------------------------
    appointment_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    walk_distance_band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    carry_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    access_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- Surveyed: autonomy fit (M5 labels) -------------------------------
    landing_surface: Mapped[str | None] = mapped_column(String(16), nullable=True)
    curb_access: Mapped[str | None] = mapped_column(String(16), nullable=True)
    door_path: Mapped[str | None] = mapped_column(String(16), nullable=True)
    obstruction: Mapped[str | None] = mapped_column(String(24), nullable=True)
    who_receives: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Where the vehicle can stop (DRV-7). One of STOP_POINTS.
    stop_point: Mapped[str | None] = mapped_column(String(16), nullable=True)

    surveyed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Who stood at the door. An unattributed judgement is not much better than
    # no judgement - these answers become `M5`'s training labels, and a bad
    # surveyor has to be findable so their rows can be discounted. Nullable:
    # profiles surveyed before this existed have no answer, and inventing one
    # would be worse than the gap.
    surveyed_by_driver_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("drivers.id"), nullable=True
    )

    @property
    def has_dwell(self) -> bool:
        return self.dwell_sample_count > 0 and self.dwell_p50_seconds is not None

    @property
    def is_surveyed(self) -> bool:
        """Whether a person has actually stood at this door.

        The autonomy columns are the ones `M5` needs, so "surveyed" means those
        are answered - not that some access note was typed at a desk.
        """
        return self.surveyed_at is not None and self.landing_surface is not None
