"""
The public tracking page's payload (docs/ROADMAP.md F3).

**This schema is a privacy boundary, not a serialization detail.** It is the
complete list of what an unauthenticated stranger holding a URL can learn, so
anything not here is not visible - no driver name or phone, no other stops on the
route, no client identity, no internal ids, no count of what else is on the van.
Adding a field here is a disclosure decision. See app/tracking/service.py for the
three rules that govern the one genuinely sensitive field, `driver_position`.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DriverPositionView(BaseModel):
    lat: float
    lng: float
    # Shown to the recipient as "updated 20 seconds ago". A stale dot with no
    # timestamp reads as a live one, which is worse than showing no dot: a
    # recipient watching a frozen marker concludes the driver is parked outside.
    recorded_at: datetime


class TrackingView(BaseModel):
    # The internal status value, for the page's own logic. The two human strings
    # below are what a person reads - `en_route_drop` is a sink's vocabulary.
    status: str
    headline: str
    detail: str
    # A truncated form of the destination, so the recipient can confirm the link
    # is about their delivery without the full street address living on a page
    # whose only credential is in the URL bar.
    destination_hint: str | None
    estimated_arrival: datetime | None
    delivered_at: datetime | None
    # Present only while this recipient's drop is the driver's CURRENT stop -
    # see app/tracking/service.py rule 1.
    driver_position: DriverPositionView | None
    # Whether the page should keep polling. False on a finished delivery, so a
    # forgotten open tab stops hitting the endpoint forever.
    is_live: bool
