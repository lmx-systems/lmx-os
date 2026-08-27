"""
Capturing what the recipient thought (docs/ROADMAP.md F13).

Lives beside the tracking page rather than in `app/delivery/`, and that is a
constraint rather than a preference: submitting a rating means resolving a tracking
token, `app/delivery/` is dispatch core, and core importing an edge module is what
`tests/test_architecture_boundaries.py` exists to refuse. The rating is a
recipient-facing capture, so it belongs on the recipient-facing side.

Four rules, each of which is a way this could quietly be wrong.

**1. Only a delivered order can be rated.** Rating something that has not arrived is
meaningless, and offering the prompt early would collect noise. A *failed* delivery is
deliberately not ratable either - there is real signal in "you never turned up", but it
is a different question from "how was the delivery", and mixing the two into one score
makes the number mean nothing. Exceptions already have their own channel in
`flag_stop_issue`.

**2. The window is the token's own life.** No separate expiry: the link already dies
`tracking_link_grace_hours` after delivery, so a recipient can rate from the moment it
arrives until the link stops working, and `resolve_tracking` enforces that for free.
Adding a second window would have meant two things to keep in step.

**3. A second submission edits the first.** A recipient who taps four stars and then
wants to add a sentence should not be blocked, and it is their own row. The unique
constraint makes that an update rather than a duplicate, so a count of ratings stays a
count of people. `first_submitted_at` is preserved so "when did they tell us" survives
a revision.

**4. Nothing here aggregates by driver.** See the model docstring: the same numbers read
as a per-driver ranking are exactly what `W4` warns against, and that view should be a
decision rather than a side effect of this file existing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.delivery_rating import (
    MAX_COMMENT_LENGTH,
    MAX_SCORE,
    MIN_SCORE,
    RECIPIENT,
    DeliveryRating,
)
from app.models.order import Order, OrderStatus

logger = structlog.get_logger(__name__)


class RatingNotAllowed(Exception):
    """The order is not in a state a recipient can rate.

    Distinct from an invalid token, which `TrackingTokenInvalid` already covers - the
    caller turns this into a 409 and that into a 404, because "this link is not real"
    and "this delivery has not happened yet" are different answers and only one of them
    is worth telling a stranger.
    """


@dataclass(frozen=True)
class RatingState:
    """What the tracking page needs to know about rating, for this holder.

    Both fields describe the reader's own situation rather than anything about the
    delivery, the driver or the client - which is what makes them safe to put on a
    payload whose docstring calls itself a privacy boundary.
    """

    can_rate: bool
    score: int | None = None
    comment: str | None = None

    @property
    def already_rated(self) -> bool:
        return self.score is not None


NOT_RATABLE = RatingState(can_rate=False)


def _is_ratable(order: Order) -> bool:
    """Rule 1. Delivered only."""
    return order.status == OrderStatus.delivered


async def rating_state(session: AsyncSession, order: Order) -> RatingState:
    """Whether this delivery can be rated, and what was said if it already was."""
    existing = (
        await session.execute(
            select(DeliveryRating).where(
                DeliveryRating.order_id == order.id,
                DeliveryRating.rated_by == RECIPIENT,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # Still `can_rate`: rule 3 allows an edit while the link lives.
        return RatingState(
            can_rate=_is_ratable(order), score=existing.score, comment=existing.comment
        )

    return RatingState(can_rate=_is_ratable(order))


def _clean_comment(comment: str | None) -> str | None:
    """Trim, cap, and treat blank as absent.

    An empty string and no comment are the same thing to a reader, and storing one as
    the other would make "did they write anything" a question about whitespace. Not
    escaped here - it is escaped where it renders, because the database should hold what
    the person typed rather than a presentation of it.
    """
    if comment is None:
        return None
    cleaned = comment.strip()
    if not cleaned:
        return None
    return cleaned[:MAX_COMMENT_LENGTH]


async def submit_rating(
    session: AsyncSession, order: Order, *, score: int, comment: str | None = None
) -> RatingState:
    """Record the recipient's rating, or update the one they already left.

    Does not commit - the caller owns the transaction.
    """
    if not _is_ratable(order):
        raise RatingNotAllowed(f"order is {order.status.value}, not delivered")
    if not MIN_SCORE <= score <= MAX_SCORE:
        # Belt to the schema's braces and the database's CHECK. A score outside the
        # scale is not a rating, and silently clamping it would invent an opinion.
        raise RatingNotAllowed(f"score must be between {MIN_SCORE} and {MAX_SCORE}")

    now = datetime.now(timezone.utc)
    cleaned = _clean_comment(comment)

    existing = (
        await session.execute(
            select(DeliveryRating).where(
                DeliveryRating.order_id == order.id,
                DeliveryRating.rated_by == RECIPIENT,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # Rule 3. Their own row, edited - and `first_submitted_at` deliberately untouched.
        existing.score = score
        existing.comment = cleaned
        logger.info(
            "delivery_rating_updated",
            order_id=str(order.id),
            score=score,
            has_comment=cleaned is not None,
        )
        return RatingState(can_rate=True, score=score, comment=cleaned)

    session.add(
        DeliveryRating(
            order_id=order.id,
            rated_by=RECIPIENT,
            score=score,
            comment=cleaned,
            first_submitted_at=now,
        )
    )
    logger.info(
        "delivery_rating_submitted",
        order_id=str(order.id),
        score=score,
        has_comment=cleaned is not None,
    )
    return RatingState(can_rate=True, score=score, comment=cleaned)
