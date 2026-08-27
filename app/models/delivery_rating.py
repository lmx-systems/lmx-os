"""
What the person who received a delivery thought of it (docs/ROADMAP.md F13).

**A separate table rather than columns on `orders`**, because a rating has an author.
The roadmap row says "prompt to the shop", and in this data model `Shop` is the
*pickup* location - the client's own branch, which experiences a collection and never
sees the delivery. The party who experiences the delivery is the recipient at the
door, and they are already holding a tracking link that stays live for
`tracking_link_grace_hours` after it arrives.

So `rated_by` exists from the start. Recipient capture is what is built; a client-side
rating is the same row with a different author, which keeps that an additive change
rather than a migration and a rework. Two columns on `orders` would have forced a
choice between them and lost the distinction either way.

**This is delivery quality, not a driver score.** The same signal read as a per-driver
ranking is the "camera pointed at me" that `W4` explicitly warns against, and nothing
here aggregates by driver. That is a deliberate omission, not an oversight - a
satisfaction number becomes a performance instrument the moment someone builds the
view, and that belongs in the conversation `W4` frames rather than falling out of a
capture mechanism.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# Who left the rating. `recipient` is the person the parts were delivered to, reached
# through their tracking link. `client` is the distributor who sent it - not captured
# anywhere yet, and present so that adding it later does not mean reinterpreting rows
# already written.
RECIPIENT = "recipient"
CLIENT = "client"
RATER_KINDS = (RECIPIENT, CLIENT)

MIN_SCORE = 1
MAX_SCORE = 5

# Matches `Stop.flag_note` and `Order.delivery_notes`. Long enough for a real sentence
# about a real problem, short enough that it cannot be used as free storage on an
# unauthenticated endpoint.
MAX_COMMENT_LENGTH = 500


class DeliveryRating(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "delivery_ratings"
    __table_args__ = (
        # One rating per order per author. A recipient changing their mind updates their
        # own row rather than adding a second one, which keeps a count of ratings equal
        # to a count of people who rated.
        UniqueConstraint("order_id", "rated_by", name="uq_delivery_rating_order_rater"),
    )

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )

    rated_by: Mapped[str] = mapped_column(String(16), nullable=False)

    # 1-5. Not a thumbs up/down: "fine" and "excellent" are different things to learn
    # from, and one tap on a five-point scale is no more work than one tap on two.
    score: Mapped[int] = mapped_column(Integer, nullable=False)

    # Optional, and the whole reason this is worth capturing beyond a number - "driver
    # could not find the loading dock" is the kind of per-shop knowledge `I3` wants as
    # structured annotation. Free text from an unauthenticated stranger, so it is
    # length-capped here and escaped wherever it renders.
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set on the first submission and left alone by later edits, so "when did they tell
    # us" survives a recipient revising their score.
    first_submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
