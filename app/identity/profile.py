"""Reading and writing what we know about a dock (`docs/ROADMAP_1.5.md` IDN-4).

Done when "dwell, hours, access and autonomy flags queryable per dock", so the
surface here is four writers and one reader, split by how each fact is known:

  `refresh_dwell_statistics`  observed, computed from stops we made
  `set_receiving_hours`       stated, by the receiver
  `set_access`                surveyed, by a driver at the door
  `set_autonomy_fit`          surveyed, the M5 labels
  `profile_for`               the reader

Nothing here predicts anything. `M1`/`PRD-5` is the dwell model and it reads
this table as one input; if a promise is ever made from `dwell_p50_seconds`
directly, that is the bug this docstring exists to prevent.
"""
from datetime import datetime, timezone

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.resolution import canonical_location
from app.models.location import Location
from app.models.receiver_profile import (
    CARRY_EFFORTS,
    CURB_ACCESS,
    DOOR_PATHS,
    LANDING_SURFACES,
    OBSTRUCTIONS,
    SOURCE_OBSERVED,
    SOURCE_STATED,
    SOURCE_SURVEYED,
    WALK_DISTANCE_BANDS,
    WHO_RECEIVES,
    ReceiverProfile,
)
from app.models.shop import Shop
from app.models.stop import Stop

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Below this, a p90 is a number with no standing. Ten completed stops is not a
# statistical threshold - MODEL_AND_DATA_BRIEF.md §"Volume and time to a usable
# model" puts a usable pooled dwell prior at ~30 observations per dock - it is
# the point below which publishing a p90 misleads more than it informs. The p50
# is still stored, with its sample count beside it, so a reader can judge.
_MIN_SAMPLES_FOR_P90 = 10


async def profile_for(
    session: AsyncSession, location: Location, *, create: bool = False
) -> ReceiverProfile | None:
    """The profile for a dock, following the alias chain first.

    An absorbed dock is not a place, so asking for its profile must return the
    surviving dock's - otherwise a merge would quietly orphan everything we knew
    about the door.
    """
    dock = await canonical_location(session, location)
    existing = await session.scalar(
        select(ReceiverProfile).where(ReceiverProfile.location_id == dock.id)
    )
    if existing is not None or not create:
        return existing

    profile = ReceiverProfile(location_id=dock.id)
    session.add(profile)
    await session.flush()
    return profile


def _completed_dwells_at(location_id) -> Select:
    """Seconds between arrival and completion, for stops that actually finished.

    Joined through `Shop.location_id` because that is how a stop reaches a dock,
    and restricted to `status == 'completed'`: a failed or flagged stop never
    produced a true dwell, only a lower bound on one (M1b). Counting those as
    observations would bias the worst docks downward, which is the one direction
    that breaks an SLA promise rather than merely being wrong.
    """
    return (
        select(
            func.extract("epoch", Stop.completed_at - Stop.arrived_at).label("dwell")
        )
        .join(Shop, Stop.shop_id == Shop.id)
        .where(
            Shop.location_id == location_id,
            Stop.status == "completed",
            Stop.arrived_at.is_not(None),
            Stop.completed_at.is_not(None),
            # A completion recorded before the arrival is a clock or a tap
            # problem, not a zero-second stop. Excluded rather than clamped:
            # clamping to zero would drag the median of a fast dock down and
            # look exactly like the minute-rounding defect we are replacing.
            Stop.completed_at >= Stop.arrived_at,
        )
    )


async def refresh_dwell_statistics(
    session: AsyncSession, location: Location
) -> ReceiverProfile:
    """Recompute this dock's observed dwell from the stops we have made.

    Percentiles are computed in Postgres rather than pulled into Python: the
    row count per dock is unbounded, and `percentile_cont` is exact over the
    whole set instead of over whatever we were willing to fetch.
    """
    dock = await canonical_location(session, location)
    profile = await profile_for(session, dock, create=True)

    dwells = _completed_dwells_at(dock.id).subquery()
    row = (
        await session.execute(
            select(
                func.count().label("n"),
                func.percentile_cont(0.5).within_group(dwells.c.dwell).label("p50"),
                func.percentile_cont(0.9).within_group(dwells.c.dwell).label("p90"),
            ).select_from(dwells)
        )
    ).one()

    censored = await session.scalar(
        select(func.count())
        .select_from(Stop)
        .join(Shop, Stop.shop_id == Shop.id)
        .where(Shop.location_id == dock.id, Stop.status == "failed")
    )

    profile.dwell_sample_count = int(row.n or 0)
    profile.dwell_censored_count = int(censored or 0)
    profile.dwell_p50_seconds = int(row.p50) if row.p50 is not None else None
    # Withheld rather than stored small: a p90 over four stops is the number a
    # reader is most likely to quote and least entitled to.
    profile.dwell_p90_seconds = (
        int(row.p90)
        if row.p90 is not None and profile.dwell_sample_count >= _MIN_SAMPLES_FOR_P90
        else None
    )
    profile.dwell_observed_at = datetime.now(timezone.utc)

    await session.flush()
    return profile


async def set_receiving_hours(
    session: AsyncSession, location: Location, hours: dict
) -> ReceiverProfile:
    """Record when this dock says it accepts deliveries.

    Shape: `{"mon": [["08:00", "12:00"], ["13:00", "17:00"]], ...}`. A day may
    carry more than one window, because a dock that shuts for lunch is one place
    with a gap, not two places. A day that is absent means "not stated"; a day
    mapped to `[]` means "closed", and the difference matters - one is a gap in
    what we know and the other is a fact.
    """
    _validate_hours(hours)
    profile = await profile_for(session, location, create=True)
    profile.receiving_hours = hours
    profile.hours_source = SOURCE_STATED
    profile.hours_stated_at = datetime.now(timezone.utc)
    await session.flush()
    return profile


def _validate_hours(hours: dict) -> None:
    """Refuse a malformed timetable rather than storing it.

    JSONB will accept anything, so this is the only place the shape is checked.
    A silently malformed day would read as "not stated" forever.
    """
    if not isinstance(hours, dict):
        raise ValueError("receiving hours must be a mapping of weekday to windows")
    for day, windows in hours.items():
        if day not in _WEEKDAYS:
            raise ValueError(f"{day!r} is not a weekday key; expected one of {_WEEKDAYS}")
        if not isinstance(windows, list):
            raise ValueError(f"{day!r} must map to a list of windows, got {type(windows)}")
        for window in windows:
            if not (isinstance(window, (list, tuple)) and len(window) == 2):
                raise ValueError(f"{day!r} has a window that is not a [open, close] pair")
            opens, closes = window
            for value in (opens, closes):
                if not (isinstance(value, str) and _looks_like_time(value)):
                    raise ValueError(f"{value!r} is not an HH:MM time")
            if opens >= closes:
                # String comparison is correct for zero-padded HH:MM, and an
                # overnight window would need a different representation than
                # this one - refusing beats storing something ambiguous.
                raise ValueError(f"{day!r} window {opens}-{closes} does not move forward")


def _looks_like_time(value: str) -> bool:
    if len(value) != 5 or value[2] != ":":
        return False
    hh, mm = value[:2], value[3:]
    return hh.isdigit() and mm.isdigit() and int(hh) <= 23 and int(mm) <= 59


async def set_access(
    session: AsyncSession,
    location: Location,
    *,
    appointment_required: bool | None = None,
    walk_distance_band: str | None = None,
    carry_effort: str | None = None,
    access_notes: str | None = None,
) -> ReceiverProfile:
    """What a driver has to do to get the parcel to the door."""
    _require_in("walk_distance_band", walk_distance_band, WALK_DISTANCE_BANDS)
    _require_in("carry_effort", carry_effort, CARRY_EFFORTS)

    profile = await profile_for(session, location, create=True)
    if appointment_required is not None:
        profile.appointment_required = appointment_required
    if walk_distance_band is not None:
        profile.walk_distance_band = walk_distance_band
    if carry_effort is not None:
        profile.carry_effort = carry_effort
    if access_notes is not None:
        profile.access_notes = access_notes
    profile.surveyed_at = datetime.now(timezone.utc)
    await session.flush()
    return profile


async def set_autonomy_fit(
    session: AsyncSession,
    location: Location,
    *,
    landing_surface: str | None = None,
    curb_access: str | None = None,
    door_path: str | None = None,
    obstruction: str | None = None,
    who_receives: str | None = None,
) -> ReceiverProfile:
    """The `M5` labels - whether anything other than a van can serve this door.

    Thirty weeks early on purpose. `MODEL_AND_DATA_BRIEF.md` is explicit that
    these cost nothing to collect now and require revisiting every dock to add
    later.
    """
    _require_in("landing_surface", landing_surface, LANDING_SURFACES)
    _require_in("curb_access", curb_access, CURB_ACCESS)
    _require_in("door_path", door_path, DOOR_PATHS)
    _require_in("obstruction", obstruction, OBSTRUCTIONS)
    _require_in("who_receives", who_receives, WHO_RECEIVES)

    profile = await profile_for(session, location, create=True)
    for field, value in (
        ("landing_surface", landing_surface),
        ("curb_access", curb_access),
        ("door_path", door_path),
        ("obstruction", obstruction),
        ("who_receives", who_receives),
    ):
        if value is not None:
            setattr(profile, field, value)
    profile.surveyed_at = datetime.now(timezone.utc)
    await session.flush()
    return profile


def _require_in(field: str, value: str | None, allowed: tuple[str, ...]) -> None:
    """Reject a value outside the vocabulary instead of storing it.

    These columns are `M5`'s labels. An unrecognised value does not fail
    anything at write time - it fails months later as a class the model has one
    example of, which is indistinguishable from noise.
    """
    if value is not None and value not in allowed:
        raise ValueError(f"{field}={value!r} is not one of {allowed}")


__all__ = [
    "SOURCE_OBSERVED",
    "SOURCE_STATED",
    "SOURCE_SURVEYED",
    "profile_for",
    "refresh_dwell_statistics",
    "set_access",
    "set_autonomy_fit",
    "set_receiving_hours",
]
