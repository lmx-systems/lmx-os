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

from app.models.delivery_rating import MAX_COMMENT_LENGTH, MAX_SCORE, MIN_SCORE

from pydantic import BaseModel, Field


class DriverPositionView(BaseModel):
    lat: float
    lng: float
    # Shown to the recipient as "updated 20 seconds ago". A stale dot with no
    # timestamp reads as a live one, which is worse than showing no dot: a
    # recipient watching a frozen marker concludes the driver is parked outside.
    recorded_at: datetime


class RecipientRatingView(BaseModel):
    """Whether this reader can rate the delivery, and what they said if they have.

    **Both fields are about the reader's own action**, which is what makes them
    admissible on a payload this file calls a privacy boundary. `can_rate` follows from
    the delivery status the page already shows, and `score`/`comment` are what this same
    link submitted - so nothing here tells a holder anything they did not either see
    already or type themselves. No driver identity, no comparison, no aggregate.
    """

    can_rate: bool
    score: int | None = None
    comment: str | None = None


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
    # Ratings (docs/ROADMAP.md F13). A deliberate addition to this boundary - see
    # RecipientRatingView for why it discloses nothing new.
    rating: RecipientRatingView


class SubmitRatingBody(BaseModel):
    """One tap, and optionally a sentence.

    Validated here as well as in `app/tracking/ratings.py` and by a CHECK on the table.
    Three layers is not redundancy for its own sake: this one returns a 422 with a
    useful message, the service protects callers that are not this endpoint, and the
    constraint is what stays true if a row is ever written by hand.
    """

    score: int = Field(ge=MIN_SCORE, le=MAX_SCORE)
    comment: str | None = Field(default=None, max_length=MAX_COMMENT_LENGTH)
