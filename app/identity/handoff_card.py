"""The handoff card: what an operator needs to serve one order's door (Proof Map O12).

Every fact on it already exists. The driver app's capture (`DE-4`) writes the
handoff point - the pins, where the vehicle stops, the door, the path, the four
guided photos - and the receiver profile holds the hours and who receives. What
did not exist was one place that puts them together for an order, which is the
unit an operator is handed. Before this, reading a door meant knowing which of
three tables to open.

**It reports, it does not judge.** No machine verdict is on the card: whether a
drone could take this order is `DE-14`'s, and `app/` never imports `ml/`. The
card is the record the verdict would be made from, with what is missing listed
apart, because the missing facts are the collection list.

**Unknown stays unknown.** A field nobody captured is null on the card and named
in `missing`; it is never filled from a neighbour or a default. A captured "no
obstructions" is an empty list, which is a different fact from null.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.handoff_capture import GOLD_FIELDS, handoff_point_for, is_captured
from app.identity.profile import profile_for
from app.identity.resolution import canonical_location
from app.models.handoff_point import HandoffPoint
from app.models.location import Location
from app.models.order import Order
from app.models.receiver_profile import ReceiverProfile


@dataclass(frozen=True)
class HandoffCard:
    order_id: uuid.UUID
    location_id: uuid.UUID | None
    address: str | None
    captured: bool
    # The gold fields nobody has captured, in GOLD_FIELDS order. Every one of
    # them when the door has no handoff point at all.
    missing: list[str]
    point: dict = field(default_factory=dict)
    photos: dict[str, str | None] = field(default_factory=dict)
    receiving_hours: dict | None = None
    hours_source: str | None = None
    who_receives: str | None = None
    appointment_required: bool | None = None
    source: str | None = None
    collected_on: date | None = None


# What the card shows of the handoff point, beside the photos. The gold fields
# and the measurements a machine operator asks about that the tier does not need.
_POINT_FIELDS = (
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
    "drone_ground_distance_m",
    "overhead",
    "door_width_cm",
    "dock_height_cm",
)


def _photos(point: HandoffPoint | None) -> dict[str, str | None]:
    return {
        "approach": point.photo_approach if point else None,
        "stop_point": point.photo_stop_point if point else None,
        "path": point.photo_path if point else None,
        "handoff": point.photo_handoff if point else None,
    }


async def handoff_card_for(session: AsyncSession, order: Order) -> HandoffCard:
    """The card for an order's drop-off door. Reads only."""
    if order.delivery_location_id is None:
        # Intake has not resolved the drop-off to a place yet, so there is no
        # door to describe - every fact is missing, the address included.
        return HandoffCard(
            order_id=order.id,
            location_id=None,
            address=None,
            captured=False,
            missing=list(GOLD_FIELDS),
            photos=_photos(None),
        )

    location = await session.get(Location, order.delivery_location_id)
    if location is None:
        raise LookupError(f"order {order.id} names location {order.delivery_location_id}, which is gone")
    dock = await canonical_location(session, location)
    point = await handoff_point_for(session, location, create=False)
    profile: ReceiverProfile | None = await profile_for(session, location)

    return HandoffCard(
        order_id=order.id,
        location_id=dock.id,
        address=dock.address,
        captured=is_captured(point),
        missing=[name for name in GOLD_FIELDS if point is None or getattr(point, name) is None],
        point={name: getattr(point, name) for name in _POINT_FIELDS} if point else {},
        photos=_photos(point),
        receiving_hours=profile.receiving_hours if profile else None,
        hours_source=profile.hours_source if profile else None,
        who_receives=profile.who_receives if profile else None,
        appointment_required=profile.appointment_required if profile else None,
        source=point.source if point else None,
        collected_on=point.collected_on if point else None,
    )
