"""What intake notices and does not refuse (`ROADMAP_1.5.md` §2.5, D-DUP and D-UNK).

D-DUP: a portal order with no reference is checked against the same client's
orders from the last ten minutes, to the same pickup and drop-off. A likely
repeat is flagged, never merged: both orders exist and the client is asked to
answer in the portal (DEC-13); a person sees it in the exception queue only if
it is unanswered with a driver on the way.

D-UNK: an ERP field the adapter does not know is accepted and flagged on the
order, where the order's explanation shows it.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from fastapi import HTTPException

from app.api.client_routes import cancel_my_order, get_my_order, say_not_a_duplicate, submit_order
from app.batch_queue.store import HoldQueueStore
from app.client_auth.dependencies import AuthedClient
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.ingestion.service import ingest_order
from app.models.client import Client
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.record.explain import explain_order
from app.reporting.exceptions import KIND_POSSIBLE_DUPLICATE, build_exception_queue
from app.schemas.client_order import ClientOrderBody

pytestmark = pytest.mark.integration

PICKUP_ADDRESS = "1200 E 6th St, Austin TX"


class FakeGeocoder(BaseGeocoder):
    provider_name = "fake"

    async def geocode(self, address: str) -> GeocodeResult | None:
        # A distinct point per address, so two drop-offs resolve to two docks.
        offset = (sum(map(ord, address)) % 1000) / 100000
        return GeocodeResult(lat=30.26 + offset, lng=-97.73 - offset, display_name=address, provider="fake")


@pytest.fixture(autouse=True)
def _stub_geocoder(monkeypatch):
    import app.api.client_routes as routes

    monkeypatch.setattr(routes, "get_geocoder", lambda: FakeGeocoder())


async def _client(db_session) -> uuid.UUID:
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="client_portal", signup_status="active")
    )
    await db_session.commit()
    return client_id


def _authed(client_id) -> AuthedClient:
    return AuthedClient(
        client_user_id=str(uuid.uuid4()), client_id=str(client_id), role="admin",
        email="jordan@example.com", name="Jordan Rivera",
    )


def _body(**overrides) -> ClientOrderBody:
    return ClientOrderBody(
        **{"pickup_address": PICKUP_ADDRESS, "drop_address": "900 Congress Ave, Austin TX", "deadline": "today", **overrides}
    )


async def test_the_same_form_sent_twice_is_flagged_and_both_orders_kept(db_session, real_redis_client):
    client_id = await _client(db_session)

    first = await submit_order(_body(), client=_authed(client_id), session=db_session)
    second = await submit_order(_body(), client=_authed(client_id), session=db_session)

    assert first.possible_duplicate_of is None
    assert second.possible_duplicate_of == first.reference
    assert second.order_id != first.order_id
    assert await db_session.scalar(select(func.count()).select_from(Order)) == 2


async def test_a_different_drop_off_is_not_a_repeat(db_session, real_redis_client):
    client_id = await _client(db_session)

    await submit_order(_body(), client=_authed(client_id), session=db_session)
    other = await submit_order(_body(drop_address="301 W 2nd St, Austin TX"), client=_authed(client_id), session=db_session)

    assert other.possible_duplicate_of is None


async def test_the_same_order_an_hour_later_is_not_a_repeat(db_session, real_redis_client):
    client_id = await _client(db_session)
    first = await submit_order(_body(), client=_authed(client_id), session=db_session)
    await db_session.execute(
        update(Order).where(Order.id == uuid.UUID(first.order_id)).values(
            created_at=datetime.now(timezone.utc) - timedelta(minutes=11)
        )
    )
    await db_session.commit()

    later = await submit_order(_body(), client=_authed(client_id), session=db_session)

    assert later.possible_duplicate_of is None


async def test_a_client_reference_is_not_second_guessed(db_session, real_redis_client):
    """Two different references are two orders the client says are different."""
    client_id = await _client(db_session)

    await submit_order(_body(reference="PO-1"), client=_authed(client_id), session=db_session)
    second = await submit_order(_body(reference="PO-2"), client=_authed(client_id), session=db_session)

    assert second.possible_duplicate_of is None


async def _set_status(db_session, order_id, status):
    await db_session.execute(update(Order).where(Order.id == uuid.UUID(order_id)).values(status=status))
    await db_session.commit()


async def _repeats(db_session) -> list[str]:
    queue = await build_exception_queue(db_session)
    return [str(i.order_id) for i in queue.items if i.kind == KIND_POSSIBLE_DUPLICATE]


async def test_the_client_is_asked_and_a_person_only_once_a_driver_is_on_the_way(db_session, real_redis_client):
    """DEC-13: the client answers in the portal while they can still cancel it.
    Unanswered with a driver on the way, it becomes a person's - and leaves the
    queue once collected."""
    client_id = await _client(db_session)
    first = await submit_order(_body(), client=_authed(client_id), session=db_session)
    second = await submit_order(_body(), client=_authed(client_id), session=db_session)

    detail = await get_my_order(second.order_id, client=_authed(client_id), session=db_session)
    assert (detail.possible_duplicate_of, detail.duplicate_answer) == (first.reference, None)
    assert await _repeats(db_session) == []

    await _set_status(db_session, second.order_id, OrderStatus.assigned)
    queue = await build_exception_queue(db_session)
    [repeat] = [i for i in queue.items if i.kind == KIND_POSSIBLE_DUPLICATE]
    assert str(repeat.order_id) == second.order_id
    assert first.reference in repeat.detail

    await _set_status(db_session, second.order_id, OrderStatus.picked_up)
    assert await _repeats(db_session) == []


async def test_both_are_real_settles_it_for_everyone(db_session, real_redis_client):
    client_id = await _client(db_session)
    await submit_order(_body(), client=_authed(client_id), session=db_session)
    second = await submit_order(_body(), client=_authed(client_id), session=db_session)
    authed = _authed(client_id)

    answered = await say_not_a_duplicate(second.order_id, client=authed, session=db_session)
    assert answered.duplicate_answer == "separate"
    order = await db_session.get(Order, uuid.UUID(second.order_id), populate_existing=True)
    [flag] = [f for f in order.intake_flags if f["kind"] == "possible_duplicate"]
    assert flag["answered_by"] == authed.client_user_id

    # A replay changes nothing, and a driver on the way raises nothing.
    again = await say_not_a_duplicate(second.order_id, client=_authed(client_id), session=db_session)
    assert again.duplicate_answer == "separate"
    order = await db_session.get(Order, uuid.UUID(second.order_id), populate_existing=True)
    assert [f for f in order.intake_flags if f["kind"] == "possible_duplicate"][0]["answered_by"] == authed.client_user_id
    await _set_status(db_session, second.order_id, OrderStatus.assigned)
    assert await _repeats(db_session) == []


async def test_yes_is_a_cancel(db_session, real_redis_client):
    client_id = await _client(db_session)
    await submit_order(_body(), client=_authed(client_id), session=db_session)
    second = await submit_order(_body(), client=_authed(client_id), session=db_session)

    cancelled = await cancel_my_order(second.order_id, client=_authed(client_id), session=db_session)

    assert cancelled.status == "cancelled"
    assert await _repeats(db_session) == []


async def test_only_a_flagged_order_of_your_own_can_be_answered(db_session, real_redis_client):
    client_id = await _client(db_session)
    first = await submit_order(_body(), client=_authed(client_id), session=db_session)
    second = await submit_order(_body(), client=_authed(client_id), session=db_session)
    stranger = await _client(db_session)

    with pytest.raises(HTTPException) as unflagged:
        await say_not_a_duplicate(first.order_id, client=_authed(client_id), session=db_session)
    assert unflagged.value.status_code == 409
    with pytest.raises(HTTPException) as not_theirs:
        await say_not_a_duplicate(second.order_id, client=_authed(stranger), session=db_session)
    assert not_theirs.value.status_code == 404


async def test_an_unknown_erp_field_is_kept_flagged_and_explained(db_session, real_redis_client):
    client_id = await _client(db_session)
    hub_id = (await db_session.get(Client, client_id)).hub_id
    db_session.add(
        Shop(client_id=client_id, name="Counter", address=PICKUP_ADDRESS, lat=30.26, lng=-97.73, external_ref="SHOP-1")
    )
    await db_session.commit()
    payload = {
        "OrderNum": "E-900", "ShipToNum": "SHOP-1", "ShipToLat": 30.26, "ShipToLng": -97.73,
        "OrderDate": "2026-10-12T09:00:00+00:00", "Weight": 2.0, "PriorityCode": "STD", "ShipVia": "VAN",
        "CustPONum": "PO-77",
    }

    order = await ingest_order(
        db_session, HoldQueueStore(), hub_id=str(hub_id), client_id=str(client_id),
        source_system="epicor", payload=payload,
    )

    assert order.intake_flags == [{"kind": "unknown_fields", "fields": ["CustPONum"]}]
    explanation = await explain_order(db_session, order_id=order.id)
    statements = [f.statement for f in explanation.facts if f.engine == "intake"]
    assert statements and "CustPONum" in statements[0]
