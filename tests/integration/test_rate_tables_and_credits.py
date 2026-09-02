"""
Rate-table billing (docs/ROADMAP.md F5) and SLA-breach credits (W3) against real Postgres.

One file because they are one surface: a credit is a percentage of a fee, so a richer fee
and a credit against it cannot be reasoned about apart.

**The finding behind W3 is that "late" was not computable.** `app/sla/engine.py` defines
HOLD windows - when we must set off - and nothing anywhere defined a delivery commitment.
`hold_deadline` is ours and internal; `promised_at` is only populated when a source hands
us one, which for an LMX-owned order is never. So a credit schedule alone would have been a
penalty with no trigger, and `client_sla_terms` is the missing half - recorded as contract
data rather than a constant chosen in a Python file.

**The tests that matter most are the ones about not quietly getting money wrong:**

  - `test_a_tier_with_no_term_is_reported_not_treated_as_clean` - "we owe nothing" and
    "nobody wrote down what we promised" are different answers.
  - `test_a_credit_never_exceeds_the_fee` - crediting more than was billed turns a
    statement into a payment.
  - `test_changing_a_rate_does_not_reprice_work_already_taken` - a card edited mid-month
    must not move numbers a client has already been quoted.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.admin_routes import list_client_rates, upsert_client_rate, upsert_client_sla_term
from app.billing.credits import assess_credits
from app.billing.rates import distance_between, price_drop
from app.billing.service import generate_invoice, invoice_detail_view
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.client_sla_term import ClientSlaTerm
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.ops_auth.dependencies import AuthedOpsUser
from app.schemas.admin import ClientRateBody, ClientSlaTermBody

pytestmark = pytest.mark.integration


def _admin() -> AuthedOpsUser:
    return AuthedOpsUser(
        ops_user_id=str(uuid.uuid4()), email="ops@lmxit.com", name="Ops", role="admin"
    )


def _rate(**overrides) -> ClientRate:
    """An unsaved rate - price_drop is pure, so most of these need no database."""
    defaults = dict(
        client_id=uuid.uuid4(),
        sla_tier="T2",
        rate_per_drop_cents=0,
        rate_per_mile_cents=0,
        rate_per_piece_cents=0,
        rate_per_weight_unit_cents=0,
        minimum_charge_cents=None,
    )
    defaults.update(overrides)
    return ClientRate(**defaults)


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
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()
    return hub_id, client_id, shop_id


async def _delivered_order(
    db_session,
    hub_id,
    client_id,
    shop_id,
    *,
    fee_cents: int = 1_800,
    sla_tier: str = "T2",
    requested_minutes_ago: int = 120,
    delivered_minutes_ago: int = 0,
    promised_at: datetime | None = None,
) -> Order:
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
        source_system="flat_file",
        raw_payload={},
        sla_tier=sla_tier,
        hold_deadline=now,
        weight_units=1,
        status=OrderStatus.delivered,
        requested_at=now - timedelta(minutes=requested_minutes_ago),
        delivered_at=now - timedelta(minutes=delivered_minutes_ago),
        promised_at=promised_at,
        fee_cents=fee_cents,
    )
    db_session.add(order)
    await db_session.commit()
    return order


async def _term(db_session, client_id, **overrides):
    defaults = dict(
        client_id=client_id,
        sla_tier="T2",
        delivery_target_minutes=60,
        credit_percent=25,
    )
    defaults.update(overrides)
    term = ClientSlaTerm(**defaults)
    db_session.add(term)
    await db_session.commit()
    return term


# ---------------------------------------------------------------------------
# F5: the rate table
# ---------------------------------------------------------------------------


def test_a_flat_per_drop_rate_prices_exactly_as_before():
    """Every existing contract. The new components default to zero, so nothing that was
    working changes."""
    priced = price_drop(_rate(rate_per_drop_cents=1_800), miles=4.2, pieces=3, weight_units=7)

    assert priced.fee_cents == 1_800


def test_a_hybrid_rate_adds_its_components():
    """**How courier rates are actually written** - "$8 plus $1.50 a mile" - which is why
    the components are additive rather than a mutually-exclusive basis."""
    priced = price_drop(
        _rate(rate_per_drop_cents=800, rate_per_mile_cents=150),
        miles=4.0,
        pieces=0,
        weight_units=0,
    )

    assert priced.fee_cents == 800 + 600


def test_mileage_is_not_rounded_before_it_is_multiplied():
    """Rounding the distance first turns a 4.4-mile drop into a 4-mile one and quietly
    under-bills every short run."""
    priced = price_drop(
        _rate(rate_per_mile_cents=150), miles=4.4, pieces=0, weight_units=0
    )

    assert priced.fee_cents == 660  # 4.4 * 150, not 4 * 150


def test_per_piece_and_per_weight_components():
    priced = price_drop(
        _rate(rate_per_drop_cents=500, rate_per_piece_cents=75, rate_per_weight_unit_cents=20),
        miles=None,
        pieces=4,
        weight_units=2.5,
    )

    assert priced.fee_cents == 500 + 300 + 50


def test_the_minimum_tops_a_cheap_drop_up():
    priced = price_drop(
        _rate(rate_per_mile_cents=150, minimum_charge_cents=1_200),
        miles=1.0,
        pieces=0,
        weight_units=0,
    )

    assert priced.fee_cents == 1_200
    # Its own line, so a client looking at a short drop can see the minimum is what they
    # are paying for rather than concluding the mileage was computed wrong.
    kinds = [c["kind"] for c in priced.breakdown["components"]]
    assert "minimum" in kinds


def test_a_missing_distance_is_not_charged_for(caplog):
    """**Charging for a distance we could not compute is the one thing a client would be
    right to dispute**, and in a bare total it would be invisible."""
    priced = price_drop(
        _rate(rate_per_drop_cents=800, rate_per_mile_cents=150),
        miles=None,
        pieces=0,
        weight_units=0,
    )

    assert priced.fee_cents == 800
    mileage = next(c for c in priced.breakdown["components"] if c["kind"] == "mileage")
    assert mileage["amount_cents"] == 0
    assert "could not be computed" in mileage["detail"]


def test_the_breakdown_explains_the_number():
    """With one flat rate, "why is this $18" needed no explanation. With a rate table it
    does, and reconstructing it later from a card that may since have changed is not an
    answer."""
    priced = price_drop(
        _rate(rate_per_drop_cents=800, rate_per_mile_cents=150),
        miles=4.0,
        pieces=0,
        weight_units=0,
    )

    assert priced.breakdown["total_cents"] == priced.fee_cents
    # The distance model is named, so the day E1 lands old and new lines are
    # distinguishable.
    assert priced.breakdown["distance_model"] == "straight_line"
    assert sum(c["amount_cents"] for c in priced.breakdown["components"]) == priced.fee_cents


def test_distance_is_none_when_either_end_is_unknown():
    assert distance_between(30.2, -97.7, None, None) is None
    assert distance_between(None, None, 30.3, -97.8) is None
    assert distance_between(30.2, -97.7, 30.3, -97.8) > 0


async def test_an_ingested_order_is_priced_from_the_rate_table(db_session, real_redis_client):
    """End to end: the rate card reaches the order, and the arithmetic travels with it."""
    from app.batch_queue.store import HoldQueueStore
    from app.geocoding.base import BaseGeocoder, GeocodeResult
    from app.ingestion.service import ingest_lmx_order
    from app.schemas.lmx_order import LMXOrder

    class _Geo(BaseGeocoder):
        provider_name = "fake"

        async def geocode(self, address):
            return GeocodeResult(lat=30.30, lng=-97.78, display_name=address, provider="fake")

    hub_id, client_id, shop_id = await _seed(db_session)
    db_session.add(
        ClientRate(
            client_id=client_id,
            sla_tier="T2",
            rate_per_drop_cents=800,
            rate_per_mile_cents=150,
        )
    )
    await db_session.commit()

    order = await ingest_lmx_order(
        db_session,
        HoldQueueStore(),
        LMXOrder(
            source_system="flat_file",
            source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
            hub_id=str(hub_id),
            client_id=str(client_id),
            shop_external_ref=(await db_session.get(Shop, shop_id)).external_ref,
            drop_address_raw="900 Congress Ave, Austin TX",
            received_at=datetime.now(timezone.utc),
        ),
        geocoder=_Geo(),
    )

    assert order.fee_cents > 800, "the mileage component should have been added"
    assert order.fee_breakdown["distance_model"] == "straight_line"
    assert {c["kind"] for c in order.fee_breakdown["components"]} == {"base", "mileage"}


async def test_changing_a_rate_does_not_reprice_work_already_taken(
    db_session, real_redis_client
):
    """**A card edited mid-month must not move numbers a client has already been quoted.**
    Pre-existing behaviour, and it matters more with a per-mile component than it did with
    a flat one."""
    hub_id, client_id, shop_id = await _seed(db_session)
    order = await _delivered_order(db_session, hub_id, client_id, shop_id, fee_cents=1_800)

    await upsert_client_rate(
        str(client_id),
        ClientRateBody(sla_tier="T2", rate_per_drop_cents=9_900),
        session=db_session,
        _admin=_admin(),
    )

    await db_session.refresh(order)
    assert order.fee_cents == 1_800


# ---------------------------------------------------------------------------
# W3: what a breach costs
# ---------------------------------------------------------------------------


async def test_a_late_delivery_is_credited(db_session, real_redis_client):
    """**A breach costs nothing today** - a delivery three hours late bills identically to
    one on time. This is the line that makes the contract real."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(db_session, client_id, delivery_target_minutes=60, credit_percent=25)
    order = await _delivered_order(
        db_session, hub_id, client_id, shop_id, fee_cents=1_800, requested_minutes_ago=180
    )

    assessment = await assess_credits(db_session, client_id=client_id, orders=[order])

    assert len(assessment.breaches) == 1
    breach = assessment.breaches[0]
    assert breach.amount_cents == 450  # 25% of 1800
    assert breach.minutes_late == 120
    assert "120 min late" in breach.reason


async def test_an_on_time_delivery_is_not_credited(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(db_session, client_id, delivery_target_minutes=180)
    order = await _delivered_order(
        db_session, hub_id, client_id, shop_id, requested_minutes_ago=60
    )

    assessment = await assess_credits(db_session, client_id=client_id, orders=[order])

    assert assessment.breaches == []


async def test_an_explicit_promise_beats_the_tier_default(db_session, real_redis_client):
    """**If we told this customer a specific time, that is the promise.** A per-tier
    default cannot override something said out loud."""
    hub_id, client_id, shop_id = await _seed(db_session)
    # A generous tier target that would say "on time"...
    await _term(db_session, client_id, delivery_target_minutes=600)
    now = datetime.now(timezone.utc)
    # ...but we promised an hour ago and delivered now.
    order = await _delivered_order(
        db_session,
        hub_id,
        client_id,
        shop_id,
        requested_minutes_ago=120,
        promised_at=now - timedelta(minutes=60),
    )

    assessment = await assess_credits(db_session, client_id=client_id, orders=[order])

    assert len(assessment.breaches) == 1
    assert assessment.breaches[0].minutes_late == 60


async def test_a_tier_with_no_term_is_reported_not_treated_as_clean(
    db_session, real_redis_client
):
    """**"We owe nothing" and "nobody wrote down what we promised" are different
    answers**, and only one is safe to put on a statement."""
    hub_id, client_id, shop_id = await _seed(db_session)
    order = await _delivered_order(
        db_session, hub_id, client_id, shop_id, requested_minutes_ago=999
    )

    assessment = await assess_credits(db_session, client_id=client_id, orders=[order])

    assert assessment.breaches == []
    assert assessment.unassessable_order_ids == [str(order.id)]


async def test_a_late_delivery_with_a_zero_credit_term_costs_nothing(
    db_session, real_redis_client
):
    """Not every SLA has teeth, and a tier with a target but no credit is a real contract
    rather than something to hide."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(db_session, client_id, delivery_target_minutes=30, credit_percent=0)
    order = await _delivered_order(
        db_session, hub_id, client_id, shop_id, requested_minutes_ago=300
    )

    assessment = await assess_credits(db_session, client_id=client_id, orders=[order])

    assert assessment.breaches == []
    # And it is NOT unassessable - we knew the promise, we just owe nothing for missing it.
    assert assessment.unassessable_order_ids == []


async def test_a_credit_never_exceeds_the_fee(db_session, real_redis_client):
    """**Crediting more than an order was billed turns a statement into a payment**, which
    is not what a service-level credit is."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(
        db_session,
        client_id,
        delivery_target_minutes=30,
        credit_percent=50,
        credit_minimum_cents=50_000,
    )
    order = await _delivered_order(
        db_session, hub_id, client_id, shop_id, fee_cents=1_800, requested_minutes_ago=300
    )

    assessment = await assess_credits(db_session, client_id=client_id, orders=[order])

    assert assessment.breaches[0].amount_cents == 1_800


async def test_the_credit_maximum_caps_it(db_session, real_redis_client):
    """A contract crediting an unbounded percentage of a tier we price high is a liability
    nobody agreed to."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(
        db_session,
        client_id,
        delivery_target_minutes=30,
        credit_percent=50,
        credit_maximum_cents=1_000,
    )
    order = await _delivered_order(
        db_session, hub_id, client_id, shop_id, fee_cents=9_000, requested_minutes_ago=300
    )

    assessment = await assess_credits(db_session, client_id=client_id, orders=[order])

    assert assessment.breaches[0].amount_cents == 1_000


async def test_terms_are_per_tier(db_session, real_redis_client):
    """A distributor paying for T1 has bought a different promise from one on T3."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(db_session, client_id, sla_tier="T1", delivery_target_minutes=30, credit_percent=50)
    await _term(db_session, client_id, sla_tier="T3", delivery_target_minutes=600, credit_percent=5)

    fast = await _delivered_order(
        db_session, hub_id, client_id, shop_id, sla_tier="T1", requested_minutes_ago=60
    )
    slow = await _delivered_order(
        db_session, hub_id, client_id, shop_id, sla_tier="T3", requested_minutes_ago=60
    )

    assessment = await assess_credits(db_session, client_id=client_id, orders=[fast, slow])

    assert [b.order.id for b in assessment.breaches] == [fast.id]


# ---------------------------------------------------------------------------
# The statement
# ---------------------------------------------------------------------------


async def test_the_invoice_nets_credits_and_shows_all_three_numbers(
    db_session, real_redis_client
):
    """**A statement showing only a net is one a client cannot reconcile** - and one that
    hid the credit would also hide the fact that we missed something."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(db_session, client_id, delivery_target_minutes=60, credit_percent=25)
    await _delivered_order(
        db_session, hub_id, client_id, shop_id, fee_cents=1_800, requested_minutes_ago=180
    )
    await _delivered_order(
        db_session, hub_id, client_id, shop_id, fee_cents=1_800, requested_minutes_ago=30
    )

    # UTC, not local: delivered_at is UTC and the invoice window is built from this,
    # so a local date makes these tests pass all afternoon and fail after ~7pm.
    today = datetime.now(timezone.utc).date()
    invoice = await generate_invoice(
        db_session, client_id, today - timedelta(days=1), today + timedelta(days=1)
    )

    assert invoice.gross_cents == 3_600
    assert invoice.credit_cents == 450
    assert invoice.total_cents == 3_150


async def test_the_statement_lists_which_orders_were_credited(db_session, real_redis_client):
    """"Which ones?" is the first question, and an aggregate answers it with "check your
    own records"."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(db_session, client_id, delivery_target_minutes=60, credit_percent=25)
    late = await _delivered_order(
        db_session, hub_id, client_id, shop_id, fee_cents=1_800, requested_minutes_ago=200
    )

    # UTC, not local: delivered_at is UTC and the invoice window is built from this,
    # so a local date makes these tests pass all afternoon and fail after ~7pm.
    today = datetime.now(timezone.utc).date()
    invoice = await generate_invoice(
        db_session, client_id, today - timedelta(days=1), today + timedelta(days=1)
    )
    detail = await invoice_detail_view(db_session, invoice)

    assert len(detail.credits) == 1
    credit = detail.credits[0]
    assert credit.order_id == str(late.id)
    assert credit.amount_cents == 450
    # The evidence, so the line is arguable with.
    assert credit.minutes_late == 140
    assert credit.promised_by
    assert credit.delivered_at


async def test_a_statement_with_no_breaches_is_unchanged(db_session, real_redis_client):
    """The common case has to stay simple."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(db_session, client_id, delivery_target_minutes=600, credit_percent=25)
    await _delivered_order(
        db_session, hub_id, client_id, shop_id, fee_cents=1_800, requested_minutes_ago=30
    )

    # UTC, not local: delivered_at is UTC and the invoice window is built from this,
    # so a local date makes these tests pass all afternoon and fail after ~7pm.
    today = datetime.now(timezone.utc).date()
    invoice = await generate_invoice(
        db_session, client_id, today - timedelta(days=1), today + timedelta(days=1)
    )

    assert invoice.gross_cents == invoice.total_cents == 1_800
    assert invoice.credit_cents == 0
    assert (await invoice_detail_view(db_session, invoice)).credits == []


async def test_the_line_item_carries_its_breakdown(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed(db_session)
    order = await _delivered_order(db_session, hub_id, client_id, shop_id, fee_cents=1_400)
    order.fee_breakdown = {"total_cents": 1_400, "components": [], "distance_model": "straight_line"}
    await db_session.commit()

    # UTC, not local: delivered_at is UTC and the invoice window is built from this,
    # so a local date makes these tests pass all afternoon and fail after ~7pm.
    today = datetime.now(timezone.utc).date()
    invoice = await generate_invoice(
        db_session, client_id, today - timedelta(days=1), today + timedelta(days=1)
    )
    detail = await invoice_detail_view(db_session, invoice)

    assert detail.line_items[0].fee_breakdown["total_cents"] == 1_400


async def test_credits_do_not_move_after_the_statement_is_issued(
    db_session, real_redis_client
):
    """If a client's terms change next quarter, last quarter's statement must not quietly
    change with them."""
    hub_id, client_id, shop_id = await _seed(db_session)
    await _term(db_session, client_id, delivery_target_minutes=60, credit_percent=25)
    await _delivered_order(
        db_session, hub_id, client_id, shop_id, fee_cents=1_800, requested_minutes_ago=200
    )
    # UTC, not local: delivered_at is UTC and the invoice window is built from this,
    # so a local date makes these tests pass all afternoon and fail after ~7pm.
    today = datetime.now(timezone.utc).date()
    invoice = await generate_invoice(
        db_session, client_id, today - timedelta(days=1), today + timedelta(days=1)
    )

    await upsert_client_sla_term(
        str(client_id),
        ClientSlaTermBody(sla_tier="T2", delivery_target_minutes=60, credit_percent=90),
        session=db_session,
        _admin=_admin(),
    )

    detail = await invoice_detail_view(db_session, invoice)
    assert detail.credits[0].amount_cents == 450


# ---------------------------------------------------------------------------
# Setting the contract data
# ---------------------------------------------------------------------------


async def test_a_rate_can_be_set_and_changed(db_session, real_redis_client):
    _hub, client_id, _shop = await _seed(db_session)

    created = await upsert_client_rate(
        str(client_id),
        ClientRateBody(sla_tier="T2", rate_per_drop_cents=800, rate_per_mile_cents=150),
        session=db_session,
        _admin=_admin(),
    )
    updated = await upsert_client_rate(
        str(client_id),
        ClientRateBody(sla_tier="T2", rate_per_drop_cents=900, rate_per_mile_cents=175),
        session=db_session,
        _admin=_admin(),
    )

    # **This assertion was inverted by T2.5 A1 (migration 0045), deliberately.** It used to
    # read `created.rate_id == updated.rate_id, "upsert, not a second row"`, which pinned
    # the behaviour that destroyed the rate card's history: an edit overwrote the row, so
    # nothing could later say what the rate had been, or which version priced a given drop.
    # A change is now a new version and the old one survives - see
    # tests/integration/test_client_rate_versioning.py.
    assert created.rate_id != updated.rate_id, "a change is a new version, not an overwrite"
    assert updated.rate_per_mile_cents == 175

    # The endpoint still answers with the rate in force, which is the part callers rely on.
    current = await list_client_rates(str(client_id), session=db_session, _admin=_admin())
    assert [(r.sla_tier, r.rate_per_drop_cents) for r in current] == [("T2", 900)]


async def test_a_contradictory_credit_range_is_refused(db_session, real_redis_client):
    _hub, client_id, _shop = await _seed(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await upsert_client_sla_term(
            str(client_id),
            ClientSlaTermBody(
                sla_tier="T2",
                delivery_target_minutes=60,
                credit_minimum_cents=5_000,
                credit_maximum_cents=1_000,
            ),
            session=db_session,
            _admin=_admin(),
        )
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# The placeholder schedule (docs/ROADMAP.md E11)
# ---------------------------------------------------------------------------


def test_every_tier_has_a_placeholder_term():
    """An empty table means no breach is assessable and the contract goes unenforced while
    looking fine - a worse kind of wrong than a number that is openly provisional. A tier
    missing from the set would recreate that hole for that tier alone."""
    from app.sla.engine import DEFAULT_HOLD_WINDOW_MINUTES
    from app.models.client_sla_term import PLACEHOLDER_SLA_TERMS

    assert {term.sla_tier for term in PLACEHOLDER_SLA_TERMS} == set(
        DEFAULT_HOLD_WINDOW_MINUTES
    )


def test_each_placeholder_target_clears_the_work_it_cannot_skip():
    """**The property that keeps the placeholders from bleeding money.** A target below
    hold window + time on the ground + travel is breached by physics, so a credit schedule
    attached to it pays out on every single order for a service level nobody sold.

    Two of the three inputs are themselves placeholders, which is exactly why the targets
    carry headroom rather than sitting on the computed floor.
    """
    from app.travel import PLACEHOLDER_STOP_SERVICE_MINUTES, minutes_for_miles
    from app.models.client_sla_term import PLACEHOLDER_SLA_TERMS
    from app.sla.engine import DEFAULT_HOLD_WINDOW_MINUTES

    unavoidable = 2 * PLACEHOLDER_STOP_SERVICE_MINUTES + minutes_for_miles(5.0)

    for term in PLACEHOLDER_SLA_TERMS:
        floor = DEFAULT_HOLD_WINDOW_MINUTES[term.sla_tier] + unavoidable
        assert term.delivery_target_minutes > floor, (
            f"{term.sla_tier}: a {term.delivery_target_minutes} min target is under the "
            f"{floor:.0f} min floor - it would be breached by physics"
        )


def test_the_placeholder_credits_never_exceed_the_fee():
    """A percentage over 100 would turn a statement into a payment. `_credit_for` clamps
    it anyway, but a schedule that relies on the clamp is one nobody has read."""
    from app.models.client_sla_term import PLACEHOLDER_SLA_TERMS

    assert all(0 <= term.credit_percent <= 100 for term in PLACEHOLDER_SLA_TERMS)


async def test_the_placeholder_schedule_makes_breaches_assessable(
    db_session, real_redis_client
):
    """The whole point of shipping provisional numbers: with them in place a late delivery
    is a credit, and without them it is silently unassessable."""
    from app.models.client_sla_term import PLACEHOLDER_SLA_TERMS

    hub_id, client_id, shop_id = await _seed(db_session)
    for placeholder in PLACEHOLDER_SLA_TERMS:
        db_session.add(
            ClientSlaTerm(
                client_id=client_id,
                sla_tier=placeholder.sla_tier,
                delivery_target_minutes=placeholder.delivery_target_minutes,
                credit_percent=placeholder.credit_percent,
            )
        )
    await db_session.commit()

    # T2's placeholder target is 3 hours; this took four.
    order = await _delivered_order(
        db_session, hub_id, client_id, shop_id, fee_cents=1_800, requested_minutes_ago=240
    )

    assessment = await assess_credits(db_session, client_id=client_id, orders=[order])

    assert assessment.unassessable_order_ids == []
    assert assessment.breaches[0].amount_cents == 450  # 25% of 1800
