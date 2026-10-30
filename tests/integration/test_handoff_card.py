"""The handoff card: one order's drop-off door, from the records that already hold it.

Proof Map O12. The card reads the handoff point (`DE-4`) and the receiver profile;
it writes nothing, fills nothing, and names what nobody has captured.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.routes import order_handoff_card
from app.identity.handoff_capture import GOLD_FIELDS
from app.identity.profile import set_receiving_hours
from app.models.handoff_point import HandoffPoint
from app.models.ops_user import VIEWER_ROLE
from app.models.stop import StopOrder
from app.ops_auth.dependencies import AuthedOpsUser
from tests.integration.test_dock_survey import _captured_door, _dropoff, _world

pytestmark = pytest.mark.integration

OPS = AuthedOpsUser(ops_user_id="u1", email="dispatcher@lmxit.com", name="Dispatcher", role=VIEWER_ROLE)


async def _order_id_for(db_session, dock):
    """A delivered order to `dock`: "own" for the world's location, None for an
    address intake could not resolve to a place."""
    hub, client, shop, driver, location = await _world(db_session)
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location if dock == "own" else dock)
    order_id = await db_session.scalar(select(StopOrder.order_id).where(StopOrder.stop_id == stop.id))
    return order_id, location


async def test_a_captured_door_is_on_the_card_with_nothing_left_to_collect(db_session):
    order_id, location = await _order_id_for(db_session, "own")
    await _captured_door(db_session, location)
    await set_receiving_hours(db_session, location, {"mon": [["08:00", "17:00"]], "sun": []})
    await db_session.commit()

    card = await order_handoff_card(order_id=order_id, session=db_session, _ops=OPS)

    assert card.captured is True
    assert card.missing == []
    assert card.location_id == location.id
    assert (card.point.handoff_type, card.point.stop_type, card.point.walk_distance_m) == ("counter", "lot", 14.0)
    # A captured "nothing in the way" is an empty list, not an unknown.
    assert card.point.obstructions == []
    assert card.photos["handoff"] == "d.jpg"
    assert card.receiving_hours == {"mon": [["08:00", "17:00"]], "sun": []}


async def test_an_unvisited_door_lists_every_fact_as_still_to_collect(db_session):
    order_id, location = await _order_id_for(db_session, "own")

    card = await order_handoff_card(order_id=order_id, session=db_session, _ops=OPS)

    assert card.captured is False
    assert card.missing == list(GOLD_FIELDS)
    assert card.address == location.address
    assert card.point.obstructions is None
    assert card.receiving_hours is None


async def test_a_partly_captured_door_names_only_what_is_missing(db_session):
    order_id, location = await _order_id_for(db_session, "own")
    db_session.add(HandoffPoint(location_id=location.id, source="lmx_app", handoff_type="side_door", stop_legal=False))
    await db_session.commit()

    card = await order_handoff_card(order_id=order_id, session=db_session, _ops=OPS)

    assert "handoff_type" not in card.missing and "stop_legal" not in card.missing
    assert "door_lat" in card.missing and "photo_handoff" in card.missing
    assert card.point.stop_legal is False


async def test_an_order_with_no_resolved_place_has_a_card_with_no_door(db_session):
    order_id, _ = await _order_id_for(db_session, None)

    card = await order_handoff_card(order_id=order_id, session=db_session, _ops=OPS)

    assert (card.location_id, card.address, card.captured) == (None, None, False)
    assert card.missing == list(GOLD_FIELDS)


async def test_an_unknown_order_is_a_404(db_session):
    with pytest.raises(HTTPException) as refused:
        await order_handoff_card(order_id=uuid.uuid4(), session=db_session, _ops=OPS)
    assert refused.value.status_code == 404
