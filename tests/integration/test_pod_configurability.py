"""
Configurable proof of delivery (docs/LMX_LINK_PLAN.md §1.2 "Proof") against real
Postgres + Redis.

`ProofRequirements` has been on the LMX Order Object since L1 and written to
`orders.proof_requirements` at ingestion since L3 — **and read by nothing.** So the
object advertised configurable proof while `complete_stop` enforced a constant.

**And the constant was "none".** `CompleteStopBody(method="photo")` with `photo_url`
left null completed the stop, and nine tests in this repo did exactly that. Proof of
delivery proved nothing, and the order record said we knew better.

The three rules, each with its own test below:

  1. the chosen method must actually carry evidence;
  2. a photo count above one is mandatory whatever the method — a signature cannot
     stand in for "four photos of named subjects";
  3. a signature requirement is additional, and a verified PIN satisfies it.

**Rule 1 rather than treating `photo_count_required=1` as a floor** is the correction
that matters. The app's model has always been "pick one of photo, signature or PIN",
and the defaults are documented as matching it — so enforcing the default count as a
floor would have made every PIN delivery also take a photo. A silent operational
change nobody asked for, and it breaks A4's PIN flow outright.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.delivery.proof import (
    ProofNotSatisfied,
    ResolvedProof,
    assert_proof_satisfied,
    resolve_stop_proof,
)
from app.models.client import Client
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder

pytestmark = pytest.mark.integration

DEFAULT = ResolvedProof(photo_count_required=1, photo_subjects=[], signature_required=False)


async def _seed(db_session):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file")
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address="220 Harbor St",
            lat=30.26,
            lng=-97.74,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()
    return hub_id, client_id, shop_id


async def _stop_with_orders(db_session, hub_id, client_id, shop_id, proofs: list[dict | None]):
    """A dropoff stop covering one order per entry in `proofs`."""
    route = Route(hub_id=hub_id, driver_id=uuid.uuid4(), status="active")
    now = datetime.now(timezone.utc)

    driver_id = uuid.uuid4()
    from app.models.driver import Driver

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
    route.driver_id = driver_id
    db_session.add(route)
    await db_session.commit()

    stop = Stop(route_id=route.id, stop_type="dropoff", status="arrived", sequence=1)
    db_session.add(stop)
    await db_session.commit()

    for proof in proofs:
        order = Order(
            hub_id=hub_id,
            client_id=client_id,
            shop_id=shop_id,
            external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
            source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
            source_system="flat_file",
            raw_payload={},
            sla_tier="T2",
            hold_deadline=now + timedelta(minutes=30),
            weight_units=1,
            status=OrderStatus.assigned,
            requested_at=now,
            proof_requirements=proof if proof is not None else {},
        )
        db_session.add(order)
        await db_session.commit()
        db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
        await db_session.commit()

    return stop


# ---------------------------------------------------------------------------
# Rule 1: the method must carry evidence
# ---------------------------------------------------------------------------


def test_a_photo_method_with_no_photo_is_refused():
    """**The hole this closes.** Nine tests in this repo used to complete a delivery
    exactly this way, and the endpoint accepted it."""
    with pytest.raises(ProofNotSatisfied, match="Take a photo"):
        assert_proof_satisfied(
            DEFAULT, method="photo", photo_urls=[], signature_url=None, pin_verified=False
        )


def test_a_signature_method_with_no_signature_is_refused():
    with pytest.raises(ProofNotSatisfied, match="Capture a signature"):
        assert_proof_satisfied(
            DEFAULT, method="signature", photo_urls=[], signature_url=None, pin_verified=False
        )


def test_an_unverified_pin_is_not_proof():
    with pytest.raises(ProofNotSatisfied, match="PIN"):
        assert_proof_satisfied(
            DEFAULT, method="pin", photo_urls=[], signature_url=None, pin_verified=False
        )


def test_the_default_does_not_force_a_photo_onto_every_method():
    """**The correction that matters.** `photo_count_required=1` means one photo IF a
    photo is the proof - the app's model has always been "pick one of three". Treating
    it as a floor would make every PIN delivery also take a photo, which is a silent
    operational change and breaks A4's PIN flow."""
    assert_proof_satisfied(
        DEFAULT, method="pin", photo_urls=[], signature_url=None, pin_verified=True
    )
    assert_proof_satisfied(
        DEFAULT,
        method="signature",
        photo_urls=[],
        signature_url="https://example.com/sig.png",
        pin_verified=False,
    )


# ---------------------------------------------------------------------------
# Rule 2: an elevated photo count is mandatory
# ---------------------------------------------------------------------------


def test_several_photos_are_required_whatever_the_method():
    """A client asking for four photos of named subjects is asking for the pictures; a
    signature cannot stand in for them."""
    required = ResolvedProof(
        photo_count_required=4,
        photo_subjects=["the shelf", "the box", "the paperwork", "the door"],
        signature_required=False,
    )

    with pytest.raises(ProofNotSatisfied) as exc_info:
        assert_proof_satisfied(
            required,
            method="signature",
            photo_urls=["a.jpg"],
            signature_url="https://example.com/sig.png",
            pin_verified=False,
        )
    assert "3 more photos" in str(exc_info.value)
    # The subjects are the whole reason a count above one exists - "four photos"
    # without saying of what produces four pictures of a doorstep.
    assert "the paperwork" in str(exc_info.value)

    assert_proof_satisfied(
        required,
        method="photo",
        photo_urls=["a.jpg", "b.jpg", "c.jpg", "d.jpg"],
        signature_url=None,
        pin_verified=False,
    )


def test_the_same_photo_sent_twice_counts_once():
    """Otherwise one photo satisfies "two photos" by being submitted in both the
    legacy `photo_url` field and the list."""
    from app.schemas.driver_app import CompleteStopBody

    body = CompleteStopBody(
        method="photo", photo_url="a.jpg", photo_urls=["a.jpg", "b.jpg"]
    )
    assert body.all_photo_urls == ["a.jpg", "b.jpg"]


def test_the_legacy_single_photo_field_still_works():
    """An older driver-app build must keep working through this change."""
    from app.schemas.driver_app import CompleteStopBody

    body = CompleteStopBody(method="photo", photo_url="only.jpg")
    assert body.all_photo_urls == ["only.jpg"]
    assert_proof_satisfied(
        DEFAULT,
        method="photo",
        photo_urls=body.all_photo_urls,
        signature_url=None,
        pin_verified=False,
    )


# ---------------------------------------------------------------------------
# Rule 3: a signature requirement, and what satisfies it
# ---------------------------------------------------------------------------


def test_a_signature_requirement_is_additional_to_the_photo():
    required = ResolvedProof(
        photo_count_required=1, photo_subjects=[], signature_required=True
    )

    with pytest.raises(ProofNotSatisfied, match="needs a signature"):
        assert_proof_satisfied(
            required,
            method="photo",
            photo_urls=["a.jpg"],
            signature_url=None,
            pin_verified=False,
        )

    assert_proof_satisfied(
        required,
        method="photo",
        photo_urls=["a.jpg"],
        signature_url="https://example.com/sig.png",
        pin_verified=False,
    )


def test_a_verified_pin_satisfies_a_signature_requirement():
    """Both answer "the right person received this", and the PIN is the stronger of
    the two: it was texted to the customer and is checked against what we issued (A4),
    whereas a signature is an image nobody verifies. Requiring both would be theatre."""
    required = ResolvedProof(
        photo_count_required=1, photo_subjects=[], signature_required=True
    )

    assert_proof_satisfied(
        required, method="pin", photo_urls=[], signature_url=None, pin_verified=True
    )


# ---------------------------------------------------------------------------
# Resolving requirements off the stop
# ---------------------------------------------------------------------------


async def test_an_order_that_says_nothing_gets_the_old_behaviour(
    db_session, real_redis_client
):
    """Every row ingested before the contract landed has an empty blob, and must
    behave exactly as the app always did."""
    hub_id, client_id, shop_id = await _seed(db_session)
    stop = await _stop_with_orders(db_session, hub_id, client_id, shop_id, [None])

    resolved = await resolve_stop_proof(db_session, stop.id)

    assert resolved.is_default


async def test_a_stated_requirement_is_read_off_the_order(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed(db_session)
    stop = await _stop_with_orders(
        db_session,
        hub_id,
        client_id,
        shop_id,
        [{"photo_count_required": 3, "photo_subjects": ["box", "label"], "signature_required": True}],
    )

    resolved = await resolve_stop_proof(db_session, stop.id)

    assert resolved.photo_count_required == 3
    assert resolved.photo_subjects == ["box", "label"]
    assert resolved.signature_required is True


async def test_a_commingled_stop_takes_the_strictest_requirement(
    db_session, real_redis_client
):
    """**One dropoff can cover several orders from sources with different rules.**
    Taking the laxest - or the first - would mean a client's signature requirement
    silently disappearing because someone else's order happened to share the van.
    Being over-strict costs a driver one extra photo; being under-strict costs a client
    the evidence they contracted for."""
    hub_id, client_id, shop_id = await _seed(db_session)
    stop = await _stop_with_orders(
        db_session,
        hub_id,
        client_id,
        shop_id,
        [
            {"photo_count_required": 1, "photo_subjects": ["box"], "signature_required": False},
            {"photo_count_required": 3, "photo_subjects": ["label"], "signature_required": True},
        ],
    )

    resolved = await resolve_stop_proof(db_session, stop.id)

    assert resolved.photo_count_required == 3
    assert resolved.signature_required is True
    # The union of subjects, so neither client's ask is dropped.
    assert set(resolved.photo_subjects) == {"box", "label"}


async def test_an_unreadable_requirement_falls_back_rather_than_stranding_the_driver(
    db_session, real_redis_client
):
    """A driver cannot fix our data from a doorstep, so malformed JSON must not make a
    stop uncompletable."""
    hub_id, client_id, shop_id = await _seed(db_session)
    stop = await _stop_with_orders(
        db_session, hub_id, client_id, shop_id, [{"photo_count_required": "four"}]
    )

    resolved = await resolve_stop_proof(db_session, stop.id)

    assert resolved.is_default


async def test_a_stop_with_no_orders_does_not_demand_proof_for_nothing(
    db_session, real_redis_client
):
    hub_id, client_id, shop_id = await _seed(db_session)
    stop = await _stop_with_orders(db_session, hub_id, client_id, shop_id, [])

    resolved = await resolve_stop_proof(db_session, stop.id)

    assert resolved.is_default


# ---------------------------------------------------------------------------
# The driver app is told up front
# ---------------------------------------------------------------------------


async def test_the_route_tells_the_app_what_proof_each_stop_needs(
    db_session, real_redis_client
):
    """**Sent with the stop rather than discovered on rejection.** A driver who learns
    at the door that this client wanted four photos has already put the box down and
    driven off."""
    from app.api.driver_routes import get_my_route
    from app.driver_auth.dependencies import AuthedDriver

    hub_id, client_id, shop_id = await _seed(db_session)
    stop = await _stop_with_orders(
        db_session,
        hub_id,
        client_id,
        shop_id,
        [{"photo_count_required": 2, "photo_subjects": ["box"], "signature_required": True}],
    )
    route = await db_session.get(Route, stop.route_id)

    view = await get_my_route(
        driver=AuthedDriver(
            driver_id=str(route.driver_id), hub_id=str(hub_id), device_id="test-device"
        ),
        session=db_session,
    )

    dropoff = next(s for s in view.stops if s.stop_type == "dropoff")
    assert dropoff.proof is not None
    assert dropoff.proof.photo_count_required == 2
    assert dropoff.proof.photo_subjects == ["box"]
    assert dropoff.proof.signature_required is True
