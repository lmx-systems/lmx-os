"""The golden handoff capture at the delivery door (`DE-4`).

`docs/ROADMAP_1.5.md` DE-4: the dock survey (`DRV-7`) becomes the capture of the
handoff point - the one door, bay or counter a delivery used, which is the row
the modality model learns from. The survey still writes the dock's profile as it
always has; this writes the same visit into `handoff_points` and links the stop
to it, so `record_tiers` sees the capture.

**Pins, never typed.** The stop pin is where the phone was at the Arrived tap or
on entering the stop's geofence; the door pin is where it was at Delivered. Both
are recorded on the stop whether or not the driver answers the survey, and the
capture copies them onto the handoff point. The walk from stop to door is the
distance between the two, not a band somebody guessed.

**The survey's existing answers map across where they mean one thing.** Its
`stop_point` was a quick answer about the whole dock, and some of its values say
both where the vehicle stops and whether that is legal; `door_path`,
`obstruction` and `landing_surface` each say one handoff fact in a coarser
vocabulary. An explicit answer to the new questions wins; a survey answer that
does not map is left unknown rather than guessed. Unknown stays null.

**One handoff point per dock for now.** A site can have several doors, and the
schema allows one row per door, but nothing yet tells two doors apart at capture
time - so the capture reuses the dock's oldest point. When the app can ask
"which door?", that is where a second row comes from.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.hub_calendar import hub_local_date
from app.identity.resolution import canonical_location
from app.models.handoff_point import (
    HANDOFF_TYPES,
    OBSTRUCTION_KINDS,
    OVERHEAD_KINDS,
    STEPS_OR_RAMPS,
    STOP_TYPES,
    HandoffPoint,
)
from app.models.hub import Hub
from app.models.location import Location
from app.models.stop import Stop

SOURCE_APP = "lmx_app"
PHOTO_SLOTS = ("approach", "stop_point", "path", "handoff")

# The survey's `stop_point` (receiver_profile.STOP_POINTS) as (stop type, legal).
# `none_legal` says it is not legal and nothing about where the van stopped.
_STOP_POINT = {
    "loading_dock": ("dock_apron", True),
    "marked_bay": ("loading_zone", True),
    "lot": ("lot", True),
    "street_legal": ("curb", True),
    "double_parked": ("curb", False),
    "none_legal": (None, False),
}
# `loading_dock` and `freight_lift` say how the item goes in, not whether there
# are steps on the way, so they do not map.
_DOOR_PATH = {"ground_level": "none", "steps": "steps", "ramp": "ramp"}
# Only the answers that are an obstruction kind. A security desk or a narrow
# door is real but not one of the four kinds; left unknown.
_OBSTRUCTION = {"none": [], "gate": ["gate"]}
_LANDING = {
    "paved_lot": True,
    "gravel": True,
    "grass": True,
    "rooftop": True,
    "street_only": False,
    "none": False,
}

# The fields `record_tiers` needs for a captured handoff point (migration 0069).
GOLD_FIELDS = (
    "handoff_type",
    "door_lat",
    "door_lng",
    "stop_lat",
    "stop_lng",
    "stop_type",
    "stop_legal",
    "walk_distance_m",
    "steps_or_ramps",
    "continuous_sidewalk",
    "obstructions",
    "drone_open_ground",
    "overhead",
    "photo_approach",
    "photo_stop_point",
    "photo_path",
    "photo_handoff",
)


class CaptureRefused(ValueError):
    """An answer outside the handoff vocabulary."""


def is_captured(point: HandoffPoint | None) -> bool:
    """Every field the tier needs, known - the Python twin of the view's test."""
    if point is None:
        return False
    for name in GOLD_FIELDS:
        value = getattr(point, name)
        if value is None:
            return False
    return isinstance(point.obstructions, list) and isinstance(point.overhead, list)


def walk_metres(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """Great-circle distance between two pins, in metres."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a_lat, a_lng, b_lat, b_lng))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(math.sqrt(h))


async def handoff_point_for(
    session: AsyncSession, location: Location, *, create: bool
) -> HandoffPoint | None:
    """The dock's handoff point: its oldest, on the dock or the one it was merged
    into, or a new one when `create`."""
    canonical = await canonical_location(session, location)
    point = (
        await session.scalars(
            select(HandoffPoint)
            .where(or_(HandoffPoint.location_id == location.id, HandoffPoint.location_id == canonical.id))
            .order_by(HandoffPoint.created_at, HandoffPoint.id)
            .limit(1)
        )
    ).first()
    if point is None and create:
        point = HandoffPoint(location_id=canonical.id, source=SOURCE_APP)
        session.add(point)
        await session.flush()
    return point


async def _the_point(session: AsyncSession, location: Location) -> HandoffPoint:
    point = await handoff_point_for(session, location, create=True)
    if point is None:  # create=True always returns one
        raise RuntimeError(f"no handoff point could be made for location {location.id}")
    return point


def _checked(name: str, value, allowed: tuple[str, ...]):
    if value is not None and value not in allowed:
        raise CaptureRefused(f"{name} must be one of {', '.join(allowed)}")
    return value


def _checked_list(name: str, values, allowed: tuple[str, ...]):
    if values is None:
        return None
    unknown = [v for v in values if v not in allowed]
    if unknown:
        raise CaptureRefused(f"{name}: unknown {', '.join(unknown)}; one of {', '.join(allowed)}")
    return sorted(set(values))


def handoff_facts(answers: dict) -> dict:
    """The handoff fields a survey's answers settle: the new questions as given,
    the old ones mapped where they mean one thing. Unknown is absent."""
    facts: dict = {}
    if answers.get("stop_point") in _STOP_POINT:
        stop_type, legal = _STOP_POINT[answers["stop_point"]]
        if stop_type is not None:
            facts["stop_type"] = stop_type
        facts["stop_legal"] = legal
    if answers.get("door_path") in _DOOR_PATH:
        facts["steps_or_ramps"] = _DOOR_PATH[answers["door_path"]]
    if answers.get("obstruction") in _OBSTRUCTION:
        facts["obstructions"] = list(_OBSTRUCTION[answers["obstruction"]])
    if answers.get("landing_surface") in _LANDING:
        facts["drone_open_ground"] = _LANDING[answers["landing_surface"]]

    # The new questions, which win over anything mapped above.
    explicit = {
        "handoff_type": _checked("handoff_type", answers.get("handoff_type"), HANDOFF_TYPES),
        "stop_type": _checked("handoff_stop_type", answers.get("handoff_stop_type"), STOP_TYPES),
        "stop_legal": answers.get("stop_legal"),
        "steps_or_ramps": _checked("steps_or_ramps", answers.get("steps_or_ramps"), STEPS_OR_RAMPS),
        "continuous_sidewalk": answers.get("continuous_sidewalk"),
        "obstructions": _checked_list("obstructions", answers.get("obstructions"), OBSTRUCTION_KINDS),
        "overhead": _checked_list("overhead", answers.get("overhead"), OVERHEAD_KINDS),
        "drone_open_ground": answers.get("drone_open_ground"),
    }
    facts.update({name: value for name, value in explicit.items() if value is not None})
    return facts


async def capture_handoff(
    session: AsyncSession, stop: Stop, location: Location, answers: dict, *, driver_id: str, hub: Hub | None
) -> HandoffPoint:
    """Write this visit into the dock's handoff point and link the stop to it.

    An overwrite of what this visit knows - a retry writes the same, a later
    visit writes what is true now - and never a blanking of what it does not.
    Flushes; the caller commits.
    """
    facts = handoff_facts(answers)
    point = await _the_point(session, location)
    # Field by field rather than setattr, so tests/test_no_write_only_columns.py
    # can see each one is written.
    point.handoff_type = facts.get("handoff_type", point.handoff_type)
    point.stop_type = facts.get("stop_type", point.stop_type)
    point.stop_legal = facts.get("stop_legal", point.stop_legal)
    point.steps_or_ramps = facts.get("steps_or_ramps", point.steps_or_ramps)
    point.continuous_sidewalk = facts.get("continuous_sidewalk", point.continuous_sidewalk)
    point.obstructions = facts.get("obstructions", point.obstructions)
    point.overhead = facts.get("overhead", point.overhead)
    point.drone_open_ground = facts.get("drone_open_ground", point.drone_open_ground)
    if stop.arrived_lat is not None and stop.arrived_lng is not None:
        point.stop_lat, point.stop_lng = stop.arrived_lat, stop.arrived_lng
    if stop.completed_lat is not None and stop.completed_lng is not None:
        point.door_lat, point.door_lng = stop.completed_lat, stop.completed_lng
    stop_lat, stop_lng, door_lat, door_lng = point.stop_lat, point.stop_lng, point.door_lat, point.door_lng
    if stop_lat is not None and stop_lng is not None and door_lat is not None and door_lng is not None:
        point.walk_distance_m = round(walk_metres(stop_lat, stop_lng, door_lat, door_lng), 1)
    point.source = SOURCE_APP
    point.collected_by = driver_id
    now = datetime.now(timezone.utc)
    point.collected_on = hub_local_date(hub, now) if hub is not None else now.date()
    stop.handoff_point_id = point.id
    await session.flush()
    return point


async def record_handoff_photo(
    session: AsyncSession, stop: Stop, location: Location, *, slot: str, url: str
) -> HandoffPoint:
    """One of the four guided photos, onto the dock's handoff point. Flushes."""
    if slot not in PHOTO_SLOTS:
        raise CaptureRefused(f"slot must be one of {', '.join(PHOTO_SLOTS)}")
    point = await _the_point(session, location)
    if slot == "approach":
        point.photo_approach = url
    elif slot == "stop_point":
        point.photo_stop_point = url
    elif slot == "path":
        point.photo_path = url
    else:
        point.photo_handoff = url
    stop.handoff_point_id = point.id
    await session.flush()
    return point
