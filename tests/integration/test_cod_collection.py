"""
Cash on delivery: collecting it, disputing it, counting it (docs/ROADMAP.md W2, story
DO-8, training case E3) against real Postgres + Redis.

The roadmap states the driver rule and then states the requirement about it: *"never
negotiate, one tap escalates to the distributor, keep moving"* — and it **must be enforced
by the UI, not by training alone.**

**`test_there_is_no_way_to_record_a_partial_payment` is that requirement.** The
enforcement is an absence: `CollectCodBody` has no amount field, so "collected" can only
mean "all of it". A driver facing a customer offering eighty against a hundred has two
paths, because a third was never built. The money is the distributor's invoice to their
own customer — nobody at LMX has authority to discount it, so a field to type a smaller
number into would hand a driver an authority they were never given.

**`test_a_cod_stop_cannot_be_completed_with_the_money_unaccounted_for` is the teeth.**
Before this a driver could mark a COD delivery done with no record of any cash changing
hands: parts gone, invoice unpaid, no dispute raised, nothing noticing.

And `COD_DISPUTE` has been a stop failure reason since the driver app was built, for a
payment mode the order object could not express — `PayerType` had no COD value at all.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_routes import cod_dispute_report
from app.api.driver_routes import (
    collect_cod,
    complete_stop,
    get_my_route,
    raise_cod_dispute,
)
from app.delivery.cod import COD_PAYER_TYPE
from app.driver_auth.dependencies import AuthedDriver
from app.models.client import Client
from app.models.cod_collection import OUTCOME_COLLECTED, OUTCOME_DISPUTED, CodCollection
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.message import Message
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.ops_auth.dependencies import AuthedOpsUser
from app.schemas.driver_app import CodDisputeBody, CollectCodBody, CompleteStopBody

pytestmark = pytest.mark.integration

POD_PHOTO = "local-capture://pod/test/photo.jpg"
AMOUNT = 12_500  # $125.00


def _admin() -> AuthedOpsUser:
    return AuthedOpsUser(
        ops_user_id=str(uuid.uuid4()), email="ops@lmxit.com", name="Ops", role="admin"
    )


async def _seed(db_session, *, shop_phone: str | None = "+15125550111"):
    hub_id, client_id, shop_id, driver_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file")
    )
    db_session.add(
        Driver(
            id=driver_id,
            hub_id=hub_id,
            name="Sam O.",
            phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=5,
        )
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Riverside Branch",
            address="220 Harbor St",
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
            phone=shop_phone,
        )
    )
    await db_session.commit()
    return hub_id, client_id, shop_id, driver_id


async def _cod_stop(
    db_session,
    hub_id,
    client_id,
    shop_id,
    driver_id,
    *,
    payer_type: str = COD_PAYER_TYPE,
    amount: int | None = AMOUNT,
    order_count: int = 1,
):
    """An arrived-at dropoff stop covering `order_count` orders."""
    route = Route(hub_id=hub_id, driver_id=driver_id, status="active")
    db_session.add(route)
    await db_session.commit()

    stop = Stop(route_id=route.id, stop_type="dropoff", status="arrived", sequence=1)
    db_session.add(stop)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    orders = []
    for _ in range(order_count):
        order = Order(
            hub_id=hub_id,
            client_id=client_id,
            shop_id=shop_id,
            external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
            source_order_ref=f"INV-{uuid.uuid4().hex[:6]}",
            source_system="flat_file",
            raw_payload={},
            sla_tier="T2",
            hold_deadline=now + timedelta(minutes=30),
            weight_units=1,
            status=OrderStatus.picked_up,
            requested_at=now,
            delivery_address="900 Congress Ave, Austin TX",
            delivery_lat=30.27,
            delivery_lng=-97.75,
            payer_type=payer_type,
            cod_amount_cents=amount,
        )
        db_session.add(order)
        await db_session.commit()
        db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
        await db_session.commit()
        orders.append(order)

    return stop, orders


def _authed(hub_id, driver_id) -> AuthedDriver:
    return AuthedDriver(
        driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device"
    )


async def _collections(db_session) -> list[CodCollection]:
    return list(
        (await db_session.execute(select(CodCollection))).scalars().all()
    )


# ---------------------------------------------------------------------------
# Never negotiate
# ---------------------------------------------------------------------------


def test_there_is_no_way_to_record_a_partial_payment():
    """**The requirement, expressed as an absence.** "Enforced by the UI, not by training
    alone" means there is no field to type a smaller number into - the amount comes off
    the order, so "collected" can only mean all of it. Nobody at LMX has authority to
    discount a distributor's invoice to their own customer."""
    assert "amount" not in CollectCodBody.model_fields
    assert "amount_collected_cents" not in CollectCodBody.model_fields
    assert set(CollectCodBody.model_fields) == {"method"}


async def test_collecting_records_the_full_amount_and_who_took_it(
    db_session, real_redis_client
):
    """Every collection names a driver, because cash in a van is a custody question
    (R1) - which is why this is a row and not a boolean on the stop."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)

    await collect_cod(
        str(stop.id),
        CollectCodBody(method="cash"),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    collection = (await _collections(db_session))[0]
    assert collection.outcome == OUTCOME_COLLECTED
    assert collection.amount_due_cents == AMOUNT
    assert collection.amount_collected_cents == AMOUNT
    assert collection.driver_id == driver_id
    assert collection.method == "cash"


async def test_a_card_is_not_an_accepted_method():
    """Taking a card on the distributor's behalf would make LMX a payment processor for
    someone else's transaction - a compliance question, not an enum value."""
    with pytest.raises(Exception):
        CollectCodBody(method="card")


async def test_collecting_twice_records_one_payment(db_session, real_redis_client):
    """A retried tap on a bad connection is not a second payment."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
    authed = _authed(hub_id, driver_id)

    await collect_cod(str(stop.id), CollectCodBody(method="cash"), driver=authed, session=db_session)
    await collect_cod(str(stop.id), CollectCodBody(method="cash"), driver=authed, session=db_session)

    assert len(await _collections(db_session)) == 1


async def test_collecting_after_a_dispute_is_refused(db_session, real_redis_client):
    """"Collected" and "disputed" cannot both be true of the same money."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
    authed = _authed(hub_id, driver_id)

    await raise_cod_dispute(str(stop.id), CodDisputeBody(), driver=authed, session=db_session)

    with pytest.raises(HTTPException) as exc_info:
        await collect_cod(
            str(stop.id), CollectCodBody(method="cash"), driver=authed, session=db_session
        )
    assert exc_info.value.status_code == 409


async def test_collecting_on_a_non_cod_order_is_refused(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(
        db_session, hub_id, client_id, shop_id, driver_id, payer_type="contract_client"
    )

    with pytest.raises(HTTPException) as exc_info:
        await collect_cod(
            str(stop.id),
            CollectCodBody(method="cash"),
            driver=_authed(hub_id, driver_id),
            session=db_session,
        )
    assert exc_info.value.status_code == 409


async def test_collecting_requires_arrival(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
    stop.status = "en_route"
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await collect_cod(
            str(stop.id),
            CollectCodBody(method="cash"),
            driver=_authed(hub_id, driver_id),
            session=db_session,
        )
    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# The money has to be accounted for
# ---------------------------------------------------------------------------


async def test_a_cod_stop_cannot_be_completed_with_the_money_unaccounted_for(
    db_session, real_redis_client
):
    """**The teeth.** Before this a driver could mark a COD delivery done with no record
    of any cash changing hands: parts gone, invoice unpaid, no dispute raised to explain
    it, and nothing anywhere noticing."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)

    with pytest.raises(HTTPException) as exc_info:
        await complete_stop(
            str(stop.id),
            CompleteStopBody(method="photo", photo_url=POD_PHOTO),
            driver=_authed(hub_id, driver_id),
            session=db_session,
        )
    assert exc_info.value.status_code == 409
    assert "125.00" in exc_info.value.detail
    assert "Don't negotiate" in exc_info.value.detail


async def test_completing_works_once_the_money_is_collected(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
    authed = _authed(hub_id, driver_id)

    await collect_cod(str(stop.id), CollectCodBody(method="cash"), driver=authed, session=db_session)
    view = await complete_stop(
        str(stop.id),
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )

    assert view.status == "completed"


async def test_a_dispute_lets_the_driver_leave(db_session, real_redis_client):
    """**"Keep moving" is part of the rule.** Holding a driver at a door until somebody
    else resolves a dispute would be the opposite of what it asks."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
    authed = _authed(hub_id, driver_id)

    await raise_cod_dispute(str(stop.id), CodDisputeBody(note="says it's the wrong price"), driver=authed, session=db_session)
    view = await complete_stop(
        str(stop.id),
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )

    assert view.status == "completed"


async def test_a_non_cod_delivery_is_unaffected(db_session, real_redis_client):
    """The overwhelming majority of stops. A guard that made every delivery slower would
    not be worth having."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(
        db_session, hub_id, client_id, shop_id, driver_id, payer_type="contract_client"
    )

    view = await complete_stop(
        str(stop.id),
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )
    assert view.status == "completed"


async def test_a_cod_order_with_no_amount_does_not_strand_the_driver(
    db_session, real_redis_client
):
    """A data problem only ops can fix must not leave a driver stuck at a door. Logged and
    skipped, and the delivery proceeds."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(
        db_session, hub_id, client_id, shop_id, driver_id, amount=None
    )

    view = await complete_stop(
        str(stop.id),
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )
    assert view.status == "completed"


async def test_every_order_on_a_commingled_stop_must_be_settled(
    db_session, real_redis_client
):
    """One dropoff can carry two COD orders, and settling one is not settling both."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, orders = await _cod_stop(
        db_session, hub_id, client_id, shop_id, driver_id, order_count=2
    )
    authed = _authed(hub_id, driver_id)

    await collect_cod(str(stop.id), CollectCodBody(method="cash"), driver=authed, session=db_session)

    # collect_cod settles every obligation on the stop, so both are covered.
    assert len(await _collections(db_session)) == 2
    view = await complete_stop(
        str(stop.id),
        CompleteStopBody(method="photo", photo_url=POD_PHOTO),
        driver=authed,
        session=db_session,
    )
    assert view.status == "completed"


# ---------------------------------------------------------------------------
# One tap escalates to the distributor
# ---------------------------------------------------------------------------


async def test_a_dispute_texts_the_distributor(db_session, real_redis_client):
    """**To the shop, not to LMX ops.** The disputed sum is their invoice to their own
    customer, so they are the only party who can decide anything about it - and routing it
    through us first costs the thing that matters, them hearing while their customer is
    still standing there."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)

    await raise_cod_dispute(
        str(stop.id),
        CodDisputeBody(note="says he was quoted 90"),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    message = (
        await db_session.execute(select(Message).where(Message.channel == "shop"))
    ).scalar_one()
    assert message.counterparty_phone == "+15125550111"
    assert "125.00" in message.body
    assert "did not negotiate" in message.body
    # The customer's own words travel, because a pattern across an account is the signal.
    assert "says he was quoted 90" in message.body

    dispute = (await _collections(db_session))[0]
    assert dispute.outcome == OUTCOME_DISPUTED
    # Not escalated, and correctly so: this deployment has no SMS provider, so the message
    # was recorded but nobody was actually texted. Marking it escalated would be a lie -
    # see test_the_report_says_why_nothing_is_escalated.
    assert dispute.escalated_at is None


async def test_a_real_send_marks_the_dispute_escalated(
    db_session, real_redis_client, monkeypatch
):
    """With a provider configured, the promise is actually kept."""
    from app.messaging import cod_notifications

    class _RealEnough:
        engine_name = "twilio"

        async def send(self, to, body):
            return "SM-fake-sid"

    monkeypatch.setattr(cod_notifications, "get_sms_client", lambda: _RealEnough())
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)

    await raise_cod_dispute(
        str(stop.id), CodDisputeBody(), driver=_authed(hub_id, driver_id), session=db_session
    )

    assert (await _collections(db_session))[0].escalated_at is not None


async def test_a_dispute_survives_a_shop_with_no_phone(db_session, real_redis_client):
    """The dispute is the record; the message is a courtesy on top of it. But it is NOT
    marked escalated, because nobody was told - and that is the state the report surfaces."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session, shop_phone=None)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)

    await raise_cod_dispute(
        str(stop.id), CodDisputeBody(), driver=_authed(hub_id, driver_id), session=db_session
    )

    dispute = (await _collections(db_session))[0]
    assert dispute.outcome == OUTCOME_DISPUTED
    assert dispute.escalated_at is None


async def test_a_dead_sms_gateway_does_not_lose_the_dispute(
    db_session, real_redis_client, monkeypatch
):
    """A driver who has already left must not be blocked by a gateway, and the record must
    survive it."""
    from app.messaging import cod_notifications

    class _Exploding:
        # engine_name = "twilio" so the configured path is taken - the point of this test
        # is a gateway that fails, not one that isn't there.
        engine_name = "twilio"

        async def send(self, to, body):
            raise RuntimeError("twilio is down")

    monkeypatch.setattr(cod_notifications, "get_sms_client", lambda: _Exploding())
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)

    await raise_cod_dispute(
        str(stop.id), CodDisputeBody(), driver=_authed(hub_id, driver_id), session=db_session
    )

    dispute = (await _collections(db_session))[0]
    assert dispute.outcome == OUTCOME_DISPUTED
    assert dispute.escalated_at is None


async def test_the_driver_sees_the_amount_before_knocking(db_session, real_redis_client):
    """A driver who learns there is money to collect while the customer is already taking
    the parts has lost the moment to ask for it."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)

    view = await get_my_route(driver=_authed(hub_id, driver_id), session=db_session)

    dropoff = next(s for s in view.stops if s.stop_type == "dropoff")
    assert len(dropoff.cod) == 1
    assert dropoff.cod[0].amount_due_cents == AMOUNT
    assert dropoff.cod[0].settled is False


async def test_a_settled_stop_stops_asking(db_session, real_redis_client):
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
    authed = _authed(hub_id, driver_id)

    await collect_cod(str(stop.id), CollectCodBody(method="check"), driver=authed, session=db_session)
    view = await get_my_route(driver=authed, session=db_session)

    dropoff = next(s for s in view.stops if s.stop_type == "dropoff")
    assert dropoff.cod[0].settled is True
    assert dropoff.cod[0].outcome == OUTCOME_COLLECTED


# ---------------------------------------------------------------------------
# The repeat-dispute report
# ---------------------------------------------------------------------------


async def test_the_report_counts_disputes_per_shop(db_session, real_redis_client):
    """**A single dispute is a bad afternoon; the same account every month is a commercial
    problem** - and it is invisible unless somebody counts."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    authed = _authed(hub_id, driver_id)

    for _ in range(3):
        stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
        await raise_cod_dispute(str(stop.id), CodDisputeBody(), driver=authed, session=db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
    await collect_cod(str(stop.id), CollectCodBody(method="cash"), driver=authed, session=db_session)

    report = await cod_dispute_report(str(hub_id), session=db_session, _admin=_admin())

    assert report.disputed_count == 3
    assert report.collected_count == 1
    assert report.disputed_amount_cents == AMOUNT * 3
    assert len(report.shops) == 1
    row = report.shops[0]
    assert row.shop_name == "Riverside Branch"
    assert row.disputed_count == 3
    # Collected count travels with it: three out of four and three out of three hundred
    # are different facts.
    assert row.collected_count == 1
    assert row.dispute_rate == pytest.approx(0.75)


async def test_a_clean_account_is_not_in_the_report(db_session, real_redis_client):
    """Including every account we serve would bury the handful that matter."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)

    await collect_cod(
        str(stop.id), CollectCodBody(method="cash"), driver=_authed(hub_id, driver_id), session=db_session
    )

    report = await cod_dispute_report(str(hub_id), session=db_session, _admin=_admin())

    assert report.collected_count == 1
    assert report.shops == []


async def test_the_worst_account_is_first(db_session, real_redis_client):
    """The point of the report is which conversation to have."""
    hub_id, client_id, quiet_shop, driver_id = await _seed(db_session)
    noisy_shop = uuid.uuid4()
    db_session.add(
        Shop(
            id=noisy_shop,
            client_id=client_id,
            name="Problem Branch",
            address="1 Trouble St",
            lat=30.2,
            lng=-97.7,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
            phone="+15125550999",
        )
    )
    await db_session.commit()
    authed = _authed(hub_id, driver_id)

    stop, _o = await _cod_stop(db_session, hub_id, client_id, quiet_shop, driver_id)
    await raise_cod_dispute(str(stop.id), CodDisputeBody(), driver=authed, session=db_session)
    for _ in range(4):
        stop, _o = await _cod_stop(db_session, hub_id, client_id, noisy_shop, driver_id)
        await raise_cod_dispute(str(stop.id), CodDisputeBody(), driver=authed, session=db_session)

    report = await cod_dispute_report(str(hub_id), session=db_session, _admin=_admin())

    assert [row.shop_name for row in report.shops] == ["Problem Branch", "Riverside Branch"]


async def test_disputes_nobody_was_told_about_are_surfaced_separately(
    db_session, real_redis_client
):
    """It breaks the promise the feature makes - "one tap escalates" - and folded into a
    total it would disappear."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session, shop_phone=None)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)

    await raise_cod_dispute(
        str(stop.id), CodDisputeBody(), driver=_authed(hub_id, driver_id), session=db_session
    )

    report = await cod_dispute_report(str(hub_id), session=db_session, _admin=_admin())

    assert report.disputed_count == 1
    assert report.unescalated_count == 1


async def test_the_report_says_why_nothing_is_escalated(db_session, real_redis_client):
    """**With no SMS provider (B5) every dispute is un-escalated**, and reporting that as
    N per-account failures would be a metric that cries wolf permanently. One
    deployment-wide fact, said once."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
    await raise_cod_dispute(
        str(stop.id), CodDisputeBody(), driver=_authed(hub_id, driver_id), session=db_session
    )

    report = await cod_dispute_report(str(hub_id), session=db_session, _admin=_admin())

    assert report.sms_configured is False
    assert report.unescalated_count == 1


async def test_the_window_excludes_older_disputes(db_session, real_redis_client):
    """The report feeds a monthly conversation, so it has to be about that month."""
    hub_id, client_id, shop_id, driver_id = await _seed(db_session)
    stop, _orders = await _cod_stop(db_session, hub_id, client_id, shop_id, driver_id)
    await raise_cod_dispute(
        str(stop.id), CodDisputeBody(), driver=_authed(hub_id, driver_id), session=db_session
    )

    old = (await _collections(db_session))[0]
    old.occurred_at = datetime.now(timezone.utc) - timedelta(days=90)
    await db_session.commit()

    report = await cod_dispute_report(str(hub_id), session=db_session, _admin=_admin())
    assert report.disputed_count == 0

    wide = await cod_dispute_report(
        str(hub_id), window_days=120, session=db_session, _admin=_admin()
    )
    assert wide.disputed_count == 1


async def test_another_hubs_disputes_are_not_counted(db_session, real_redis_client):
    hub_a, client_a, shop_a, driver_a = await _seed(db_session)
    hub_b, client_b, shop_b, driver_b = await _seed(db_session)

    stop, _orders = await _cod_stop(db_session, hub_b, client_b, shop_b, driver_b)
    await raise_cod_dispute(
        str(stop.id), CodDisputeBody(), driver=_authed(hub_b, driver_b), session=db_session
    )

    report = await cod_dispute_report(str(hub_a), session=db_session, _admin=_admin())
    assert report.disputed_count == 0
