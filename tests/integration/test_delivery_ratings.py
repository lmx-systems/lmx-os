"""
Recipient ratings (docs/ROADMAP.md F13), against real Postgres + Redis.

A one-tap score from the person who actually received the delivery, captured through
the tracking link they already have. The roadmap row says "prompt to the shop", which
does not survive contact with the data model: `Shop` is the *pickup* location and never
sees the delivery. So the author is recorded on every row and the recipient path is the
one built - see `app/models/delivery_rating.py`.

The tests that carry the design:

  - **`test_an_undelivered_order_cannot_be_rated`** and its failed-delivery sibling.
    Rating something that has not arrived is meaningless, and folding "you never turned
    up" into a satisfaction score makes the number mean nothing.
  - **`test_a_second_submission_edits_the_first`.** A recipient who taps four stars and
    then wants to explain why must not be blocked, and it must not become two ratings -
    a count of rows has to stay a count of people.
  - **`test_an_expired_link_cannot_rate`.** The rating window is the token's own life.
    No second expiry to keep in step.
  - **`test_the_page_never_learns_anything_new`.** `app/schemas/tracking.py` calls
    itself a privacy boundary, so a field added there is a disclosure decision.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.public_routes import rate_delivery, track_delivery
from app.config import settings
from app.models.client import Client
from app.models.delivery_rating import RECIPIENT, DeliveryRating
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.tracking import SubmitRatingBody
from app.tracking.service import ensure_tracking_token

pytestmark = pytest.mark.integration


class _FakeRequest:
    def __init__(self, ip: str) -> None:
        self.client = type("C", (), {"host": ip})()


def _ip() -> str:
    """A fresh IP per test - the tracking limiter is real Redis and would otherwise
    carry state between tests."""
    return f"198.51.100.{uuid.uuid4().int % 250}"


async def _delivered_order(db_session, *, status=OrderStatus.delivered, delivered_ago_hours=0):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="client_portal")
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address="220 Harbor St",
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()

    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
        source_system="client_portal",
        raw_payload={},
        sla_tier="T2",
        hold_deadline=now,
        weight_units=1,
        status=status,
        requested_at=now - timedelta(hours=2),
        delivered_at=(
            now - timedelta(hours=delivered_ago_hours)
            if status == OrderStatus.delivered
            else None
        ),
        delivery_address="900 Congress Ave, Austin TX",
        delivery_contact_name="Dana Whitfield",
        delivery_lat=30.30,
        delivery_lng=-97.80,
    )
    db_session.add(order)
    await db_session.commit()

    token = await ensure_tracking_token(db_session, order)
    await db_session.commit()
    return order, token, client_id


async def _rate(db_session, token, score, comment=None):
    return await rate_delivery(
        token=token,
        body=SubmitRatingBody(score=score, comment=comment),
        request=_FakeRequest(_ip()),
        session=db_session,
    )


# ---------------------------------------------------------------------------
# Capturing it
# ---------------------------------------------------------------------------


async def test_a_recipient_can_rate_a_delivered_order(db_session, real_redis_client):
    order, token, _ = await _delivered_order(db_session)

    view = await _rate(db_session, token, 4, "Left it with the service desk, no problem.")
    assert view.rating.score == 4
    assert view.rating.comment == "Left it with the service desk, no problem."

    row = (await db_session.execute(select(DeliveryRating))).scalars().one()
    assert row.order_id == order.id
    assert row.rated_by == RECIPIENT
    assert row.first_submitted_at is not None


async def test_the_prompt_only_appears_once_delivered(db_session, real_redis_client):
    """`can_rate` comes from the server, so the page never reasons about statuses."""
    _, token, _ = await _delivered_order(db_session, status=OrderStatus.en_route_drop)
    view = await track_delivery(token=token, request=_FakeRequest(_ip()), session=db_session)
    assert view.rating.can_rate is False
    assert view.rating.score is None


async def test_a_score_only_rating_needs_no_comment(db_session, real_redis_client):
    """One tap is the whole ask. Requiring prose would cost the response rate the
    feature depends on."""
    _, token, _ = await _delivered_order(db_session)
    view = await _rate(db_session, token, 5)
    assert view.rating.score == 5
    assert view.rating.comment is None


async def test_a_blank_comment_is_stored_as_absent(db_session, real_redis_client):
    """An empty string and no comment are the same thing to a reader, so "did they write
    anything" must not become a question about whitespace."""
    _, token, _ = await _delivered_order(db_session)
    await _rate(db_session, token, 3, "   ")
    row = (await db_session.execute(select(DeliveryRating))).scalars().one()
    assert row.comment is None


# ---------------------------------------------------------------------------
# Refusing it
# ---------------------------------------------------------------------------


async def test_an_undelivered_order_cannot_be_rated(db_session, real_redis_client):
    """409, and nothing written. A legitimate holder can be in this state, and they
    already saw the status on the page - so telling them is the helpful answer."""
    _, token, _ = await _delivered_order(db_session, status=OrderStatus.picked_up)

    with pytest.raises(HTTPException) as exc:
        await _rate(db_session, token, 5)
    assert exc.value.status_code == 409

    count = (await db_session.execute(select(func.count()).select_from(DeliveryRating))).scalar_one()
    assert count == 0


async def test_a_failed_delivery_cannot_be_rated(db_session, real_redis_client):
    """Deliberate, and arguable. There is real signal in "you never turned up", but it
    is a different question from "how was the delivery" - and one score covering both
    means nothing. Exceptions have their own channel in `flag_stop_issue`."""
    _, token, _ = await _delivered_order(db_session, status=OrderStatus.delivery_failed)

    with pytest.raises(HTTPException) as exc:
        await _rate(db_session, token, 1)
    assert exc.value.status_code == 409


async def test_an_unknown_token_is_a_404_not_a_409(db_session, real_redis_client):
    """Same reasoning as the read: a distinguishable response confirms a guess."""
    with pytest.raises(HTTPException) as exc:
        await _rate(db_session, "not-a-real-token", 5)
    assert exc.value.status_code == 404


async def test_an_expired_link_cannot_rate(db_session, real_redis_client, monkeypatch):
    """The rating window is the token's own life - no second expiry to keep in step.

    Delivered longer ago than the grace period, so `resolve_tracking` refuses before the
    rating logic is ever reached, and it refuses identically to an unknown token.
    """
    monkeypatch.setattr(settings, "tracking_link_grace_hours", 24)
    _, token, _ = await _delivered_order(db_session, delivered_ago_hours=48)

    with pytest.raises(HTTPException) as exc:
        await _rate(db_session, token, 5)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("score", [0, 6, -1, 99])
async def test_a_score_off_the_scale_is_refused(db_session, real_redis_client, score):
    """The schema rejects it before the endpoint runs. A 1-5 scale that accepts 9 is not
    a 1-5 scale, and clamping would invent an opinion."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SubmitRatingBody(score=score)


async def test_an_over_long_comment_is_refused(db_session, real_redis_client):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SubmitRatingBody(score=4, comment="x" * 501)


# ---------------------------------------------------------------------------
# Editing it
# ---------------------------------------------------------------------------


async def test_a_second_submission_edits_the_first(db_session, real_redis_client):
    """One row, not two. A count of ratings has to stay a count of people."""
    _, token, _ = await _delivered_order(db_session)

    await _rate(db_session, token, 2)
    view = await _rate(db_session, token, 5, "Actually they came back and sorted it.")

    assert view.rating.score == 5
    rows = (await db_session.execute(select(DeliveryRating))).scalars().all()
    assert len(rows) == 1
    assert rows[0].score == 5
    assert rows[0].comment == "Actually they came back and sorted it."


async def test_the_first_submission_time_survives_an_edit(db_session, real_redis_client):
    """So "when did they tell us" is not rewritten by a recipient revising a score."""
    _, token, _ = await _delivered_order(db_session)
    await _rate(db_session, token, 3)
    original = (await db_session.execute(select(DeliveryRating))).scalars().one().first_submitted_at

    await _rate(db_session, token, 4, "Adding a note.")
    row = (await db_session.execute(select(DeliveryRating))).scalars().one()
    assert row.first_submitted_at == original
    assert row.score == 4


async def test_the_page_shows_a_previous_rating_back(db_session, real_redis_client):
    """So a returning reader sees what they said rather than an empty prompt."""
    _, token, _ = await _delivered_order(db_session)
    await _rate(db_session, token, 4, "Fine.")

    view = await track_delivery(token=token, request=_FakeRequest(_ip()), session=db_session)
    assert view.rating.score == 4
    assert view.rating.comment == "Fine."
    # Still ratable, because an edit is allowed while the link lives.
    assert view.rating.can_rate is True


# ---------------------------------------------------------------------------
# The privacy boundary, and who gets to see it
# ---------------------------------------------------------------------------


async def test_the_page_never_learns_anything_new(db_session, real_redis_client):
    """`app/schemas/tracking.py` is an exhaustive list of what a stranger with a URL can
    learn, so adding to it is a disclosure decision.

    This asserts the shape of that decision: the rating block carries only the reader's
    own action, and no field was added that names a driver, a client, or any other stop.
    """
    _, token, _ = await _delivered_order(db_session)
    await _rate(db_session, token, 4, "Fine.")
    view = await track_delivery(token=token, request=_FakeRequest(_ip()), session=db_session)

    assert set(view.rating.model_dump()) == {"can_rate", "score", "comment"}
    payload = view.model_dump()
    for forbidden in ("driver_name", "driver_id", "client_name", "client_id", "route_id", "stops"):
        assert forbidden not in payload


async def test_the_client_sees_what_their_customer_said(db_session, real_redis_client):
    """The commercial half. The distributor owns the relationship, so they get the
    words - on the detail view, not as a column in the list."""
    from app.api.client_routes import get_my_order
    from app.client_auth.dependencies import AuthedClient
    from app.models.client_user import CLIENT_MEMBER_ROLE

    order, token, client_id = await _delivered_order(db_session)
    await _rate(db_session, token, 2, "Waited an hour past the window.")

    authed = AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="counter@example.com",
        name="Alex at the counter",
        role=CLIENT_MEMBER_ROLE,
    )
    detail = await get_my_order(order_id=str(order.id), client=authed, session=db_session)

    assert detail.rating is not None
    assert detail.rating.score == 2
    assert detail.rating.comment == "Waited an hour past the window."


async def test_an_unrated_order_reports_no_rating_to_the_client(db_session, real_redis_client):
    from app.api.client_routes import get_my_order
    from app.client_auth.dependencies import AuthedClient
    from app.models.client_user import CLIENT_MEMBER_ROLE

    order, _, client_id = await _delivered_order(db_session)
    authed = AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="counter@example.com",
        name="Alex",
        role=CLIENT_MEMBER_ROLE,
    )
    detail = await get_my_order(order_id=str(order.id), client=authed, session=db_session)
    assert detail.rating is None
