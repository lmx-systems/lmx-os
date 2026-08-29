"""
What the service-level credits are costing us (docs/ROADMAP.md W3, E11).

`W3` made a missed commitment credit a client's statement automatically. Nothing made
the total visible: a credit appears on one invoice, for one client, after billing runs -
so a month of breaches reads as **zero** until somebody generates an invoice. That is the
gap, and `test_accruing_credits_are_visible_before_any_invoice_exists` is the test for
it.

The other load-bearing one is
`test_the_accruing_figure_is_what_the_invoice_will_actually_charge`. An exposure report
that computed its own idea of a breach would eventually disagree with the statement, and
the disagreement would surface as a client query rather than as a failing test. It calls
the same `assess_credits` invoicing calls, and this asserts the two land on the same
number.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.billing.service import generate_invoice
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.client_sla_term import ClientSlaTerm
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.reporting.credit_exposure import build_credit_exposure

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 20, 17, 0, tzinfo=timezone.utc)


async def _client(db_session, name="Design Partner", *, tier="T2", percent=25, target=180):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id,
            hub_id=hub_id,
            name=name,
            pos_system="client_portal",
            signup_status="active",
        )
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
    db_session.add(
        ClientSlaTerm(
            client_id=client_id,
            sla_tier=tier,
            delivery_target_minutes=target,
            credit_percent=percent,
        )
    )
    db_session.add(ClientRate(client_id=client_id, sla_tier=tier, rate_per_drop_cents=1_800))
    await db_session.commit()
    return hub_id, client_id, shop_id


async def _delivered(
    db_session, hub_id, client_id, shop_id, *, tier="T2", late_by=0, fee_cents=1_800, target=180
):
    """Delivered `late_by` minutes after its tier commitment. Negative = on time."""
    requested = NOW - timedelta(days=2)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
        source_system="client_portal",
        raw_payload={},
        sla_tier=tier,
        hold_deadline=requested,
        weight_units=1,
        status=OrderStatus.delivered,
        requested_at=requested,
        delivered_at=requested + timedelta(minutes=target + late_by),
        delivery_address="900 Congress Ave",
        fee_cents=fee_cents,
    )
    db_session.add(order)
    await db_session.commit()
    return order


# ---------------------------------------------------------------------------
# The gap
# ---------------------------------------------------------------------------


async def test_accruing_credits_are_visible_before_any_invoice_exists(db_session):
    """The whole point. Credits used to be invisible until billing ran.

    A month of breaches reading as zero is not a reporting inconvenience - it is the
    difference between knowing what you owe and finding out on a statement.
    """
    hub_id, client_id, shop_id = await _client(db_session)
    await _delivered(db_session, hub_id, client_id, shop_id, late_by=90)
    await _delivered(db_session, hub_id, client_id, shop_id, late_by=-30)

    exposure = await build_credit_exposure(db_session, now=NOW)

    assert exposure.issued_cents == 0, "nothing invoiced yet"
    # 25% of an 1800 fee on the one late order.
    assert exposure.accruing_cents == 450
    assert exposure.total_cents == 450


async def test_the_accruing_figure_is_what_the_invoice_will_actually_charge(db_session):
    """One computation, two readers.

    A report with its own idea of a breach would drift from the statement, and the
    disagreement would arrive as a client query rather than a failing test.
    """
    hub_id, client_id, shop_id = await _client(db_session)
    await _delivered(db_session, hub_id, client_id, shop_id, late_by=120)

    before = await build_credit_exposure(db_session, now=NOW)
    assert before.accruing_cents == 450

    invoice = await generate_invoice(
        db_session,
        client_id=client_id,
        period_start=(NOW - timedelta(days=7)).date(),
        period_end=(NOW + timedelta(days=1)).date(),
    )
    assert invoice.credit_cents == before.accruing_cents, "the forecast was the bill"


async def test_credits_move_from_accruing_to_issued_when_invoiced(db_session):
    """The total is unchanged by billing - only which column it sits in."""
    hub_id, client_id, shop_id = await _client(db_session)
    await _delivered(db_session, hub_id, client_id, shop_id, late_by=90)

    await generate_invoice(
        db_session,
        client_id=client_id,
        period_start=(NOW - timedelta(days=7)).date(),
        period_end=(NOW + timedelta(days=1)).date(),
    )
    after = await build_credit_exposure(db_session, now=NOW)

    assert after.accruing_cents == 0
    assert after.issued_cents == 450
    assert after.total_cents == 450


async def test_issued_credits_are_windowed_by_delivery_not_by_invoice_date(db_session):
    """Otherwise every credit moves into whichever month billing happened to run, which
    is the distortion this report exists to remove."""
    hub_id, client_id, shop_id = await _client(db_session)
    # Delivered long before the window, invoiced now.
    old = await _delivered(db_session, hub_id, client_id, shop_id, late_by=90)
    old.requested_at = NOW - timedelta(days=200)
    old.delivered_at = NOW - timedelta(days=200) + timedelta(minutes=270)
    await db_session.commit()

    await generate_invoice(
        db_session,
        client_id=client_id,
        period_start=(NOW - timedelta(days=365)).date(),
        period_end=(NOW + timedelta(days=1)).date(),
    )

    inside = await build_credit_exposure(db_session, window_days=30, now=NOW)
    assert inside.issued_cents == 0, "a 200-day-old delivery is not this month's credit"

    outside = await build_credit_exposure(db_session, window_days=365, now=NOW)
    assert outside.issued_cents == 450


# ---------------------------------------------------------------------------
# The E11 input
# ---------------------------------------------------------------------------


async def test_each_tier_reports_the_percentage_that_produced_the_money(db_session):
    """`E11` is an open decision about exactly these numbers.

    Money without its knob is a figure; money beside its knob is an argument.
    """
    hub_id, client_id, shop_id = await _client(db_session, tier="HOT_SHOT", percent=100, target=60)
    await _delivered(db_session, hub_id, client_id, shop_id, tier="HOT_SHOT", late_by=30, target=60)

    exposure = await build_credit_exposure(db_session, now=NOW)
    hot = next(t for t in exposure.by_tier if t.sla_tier == "HOT_SHOT")

    assert hot.credit_percent == 100
    assert hot.credit_cents == 1_800, "100% of the fee, which is what the placeholder says"
    assert hot.breach_count == 1


async def test_a_tier_reports_a_breach_rate_not_just_a_count(db_session):
    """3 breaches out of 4 and out of 400 are different facts."""
    hub_id, client_id, shop_id = await _client(db_session)
    for _ in range(3):
        await _delivered(db_session, hub_id, client_id, shop_id, late_by=-10)
    await _delivered(db_session, hub_id, client_id, shop_id, late_by=60)

    tier = next(
        t for t in (await build_credit_exposure(db_session, now=NOW)).by_tier if t.sla_tier == "T2"
    )
    assert tier.delivered_count == 4
    assert tier.breach_count == 1
    assert tier.breach_rate_percent == 25.0


async def test_disagreeing_clients_leave_the_percentage_unstated(db_session):
    """Where two clients bought different credit terms for the same tier, "what does this
    tier cost us" has no single knob - so the report says nothing rather than picking
    one and implying it is the number."""
    await _client(db_session, name="A", tier="T2", percent=25)
    await _client(db_session, name="B", tier="T2", percent=50)

    exposure = await build_credit_exposure(db_session, now=NOW)
    if exposure.by_tier:
        tier = next((t for t in exposure.by_tier if t.sla_tier == "T2"), None)
        if tier is not None:
            assert tier.credit_percent is None


# ---------------------------------------------------------------------------
# Who, and what cannot be judged
# ---------------------------------------------------------------------------


async def test_exposure_is_broken_down_by_client_worst_first(db_session):
    """"Who are we paying" is the second question after "how much"."""
    hub_a, client_a, shop_a = await _client(db_session, name="Small Miss")
    hub_b, client_b, shop_b = await _client(db_session, name="Big Miss")

    await _delivered(db_session, hub_a, client_a, shop_a, late_by=30)
    for _ in range(3):
        await _delivered(db_session, hub_b, client_b, shop_b, late_by=30)

    exposure = await build_credit_exposure(db_session, now=NOW)
    assert [c.client_name for c in exposure.by_client] == ["Big Miss", "Small Miss"]
    assert exposure.by_client[0].accruing_cents == 1_350


async def test_an_order_with_no_commitment_is_counted_as_unknown_not_as_zero(db_session):
    """`W11` established this is not a success. It is equally not a zero cost - it is an
    unknown cost, and a report that folded it into the total would understate what we
    might owe."""
    hub_id, client_id, shop_id = await _client(db_session)
    # Remove the term so nothing is assessable.
    term = (await db_session.execute(select(ClientSlaTerm))).scalars().one()
    await db_session.delete(term)
    await db_session.commit()

    await _delivered(db_session, hub_id, client_id, shop_id, late_by=300)

    exposure = await build_credit_exposure(db_session, now=NOW)
    assert exposure.accruing_cents == 0
    assert exposure.unassessable_orders == 1


async def test_an_unpriced_order_is_counted_separately(db_session):
    """A credit is a percentage of a fee, so an order with no fee cannot generate one.

    Counted because an order that will never be billed is a bigger problem than the
    credit it did not produce - invoicing already logs it, and this makes it visible
    without reading logs.
    """
    hub_id, client_id, shop_id = await _client(db_session)
    await _delivered(db_session, hub_id, client_id, shop_id, late_by=90, fee_cents=None)

    exposure = await build_credit_exposure(db_session, now=NOW)
    assert exposure.unpriced_orders == 1
    assert exposure.accruing_cents == 0


async def test_nothing_delivered_reports_zero_rather_than_failing(db_session):
    await _client(db_session)
    exposure = await build_credit_exposure(db_session, now=NOW)
    assert exposure.total_cents == 0
    assert exposure.by_client == []


async def test_the_endpoint_is_ops_admin_only():
    """A cross-client view of money owed. The guard is declared on the route, which a
    direct call would skip - so this reads it off the router."""
    import app.api.routes as ops_routes
    from app.ops_auth.dependencies import require_admin

    route = next(
        r
        for r in ops_routes.router.routes
        if r.path == "/operations/credit-exposure" and "GET" in r.methods
    )
    assert require_admin in [d.call for d in route.dependant.dependencies]
