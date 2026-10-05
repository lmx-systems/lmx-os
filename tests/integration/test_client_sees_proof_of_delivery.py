"""A client sees how its delivered order was proved.

The driver's photo and the recipient's signature have been stored since the app
had a camera, and were shown only to the recipient, on the tracking page. The
client whose order it was, who answers when their customer says it never came,
could see neither.
"""
import uuid

import pytest
from sqlalchemy import select

from app.api.client_routes import get_my_order
from app.client_auth.dependencies import AuthedClient
from app.models.client_user import CLIENT_ADMIN_ROLE
from app.models.order import OrderStatus
from app.models.stop import Stop, StopOrder
from tests.integration import test_customer_tracking as tracking

pytestmark = pytest.mark.integration


def _signed_in_as(client_id) -> AuthedClient:
    return AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="dispatch@example.com",
        name="Dispatch desk",
        role=CLIENT_ADMIN_ROLE,
    )


async def _delivered(db_session, *, photo=None, signature=None, **dropoff_fields):
    hub_id, client_id, shop_id, driver_id = await tracking._seed(db_session)
    order = await tracking._delivered_with_photo(
        db_session, hub_id, client_id, shop_id, driver_id, photo, signature=signature
    )
    dropoff = (
        await db_session.execute(
            select(Stop)
            .join(StopOrder, StopOrder.stop_id == Stop.id)
            .where(StopOrder.order_id == order.id, Stop.stop_type == "dropoff")
        )
    ).scalar_one()
    for field, value in dropoff_fields.items():
        setattr(dropoff, field, value)
    await db_session.commit()
    return order, client_id


async def test_a_delivered_order_shows_its_client_the_proof(db_session, photo_bucket):
    photo = photo_bucket + "pod/a/b/photo-c.jpg"
    signature = photo_bucket + "pod/a/b/signature-c.png"
    order, client_id = await _delivered(
        db_session,
        photo=photo,
        signature=signature,
        pod_method="signature",
        pod_left_at="with reception",
    )

    detail = await get_my_order(str(order.id), client=_signed_in_as(client_id), session=db_session)

    assert detail.proof.method == "signature"
    assert detail.proof.left_at == "with reception"
    # The bucket is private, so what was stored opens for nobody: both go out signed.
    [served_photo] = detail.proof.photo_urls
    for stored, served in ((photo, served_photo), (signature, detail.proof.signature_url)):
        assert served.startswith(stored + "?")
        assert "X-Amz-Signature=" in served


async def test_every_photo_is_shown_not_just_the_first(db_session, photo_bucket):
    """An order can require several photos, and the one that settles a dispute
    needn't be the first."""
    first, second = (photo_bucket + f"pod/a/b/photo-{n}.jpg" for n in (1, 2))
    order, client_id = await _delivered(
        db_session, photo=first, pod_method="photo", pod_photo_urls=[first, second]
    )

    detail = await get_my_order(str(order.id), client=_signed_in_as(client_id), session=db_session)

    assert [url.split("?")[0] for url in detail.proof.photo_urls] == [first, second]


async def test_a_pin_is_named_and_never_shown(db_session):
    """A PIN proves the delivery by being a secret between the driver and the
    recipient, so the method is all the client is told."""
    order, client_id = await _delivered(
        db_session,
        pod_method="pin",
        pod_pin="4821",
        delivery_pin="4821",
        pod_left_at="front desk",
    )

    detail = await get_my_order(str(order.id), client=_signed_in_as(client_id), session=db_session)

    assert detail.proof.model_dump() == {
        "method": "pin",
        "photo_urls": [],
        "signature_url": None,
        "left_at": "front desk",
    }


async def test_an_order_not_yet_delivered_has_no_proof(db_session):
    hub_id, client_id, shop_id, driver_id = await tracking._seed(db_session)
    order = await tracking._order(
        db_session, hub_id, client_id, shop_id, status=OrderStatus.en_route_drop
    )
    await tracking._route_with_stops(
        db_session,
        hub_id,
        driver_id,
        stops=[(order, "pickup", "completed"), (order, "dropoff", "pending")],
    )

    detail = await get_my_order(str(order.id), client=_signed_in_as(client_id), session=db_session)

    assert detail.proof is None
