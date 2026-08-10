"""
The LMX Link scorecard (docs/LMX_LINK_PLAN.md §3.4) against real Postgres.

§3.4 names five success metrics. Every one of them has been quoted in updates since
the plan was written and none was answerable. Three now are.

**The tests that matter most are the ones about honesty rather than arithmetic:**

  - a metric with no data says so, and distinguishes "no traffic yet" from "we don't
    record this" - the first resolves itself, the second needs someone to build
    something;
  - the two metrics that cannot be computed are reported as not measured rather than
    dropped or filled with a plausible zero;
  - entry time EXCLUDES each client's first order, because including it measures
    onboarding and would make the number look worse the more new clients we win.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.models.client import Client
from app.models.client_webhook import (
    DELIVERY_DELIVERED,
    DELIVERY_PENDING,
    ClientWebhookEndpoint,
    WebhookDelivery,
    new_webhook_secret,
)
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.reporting.lmx_link import build_scorecard

pytestmark = pytest.mark.integration


def _by_name(scorecard, name: str):
    return next(m for m in scorecard.measurements if m.name.startswith(name))


async def _seed(db_session, *, approved_at: datetime | None = None):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id,
            hub_id=hub_id,
            name=f"Client {client_id.hex[:5]}",
            pos_system="client_portal",
            signup_status="active",
            approved_at=approved_at,
        )
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


async def _order(
    db_session,
    hub_id,
    client_id,
    shop_id,
    *,
    entry_seconds: int | None = None,
    source_system: str = "client_portal",
    requested_at: datetime | None = None,
    delivered_at: datetime | None = None,
) -> Order:
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
        source_system=source_system,
        raw_payload={},
        sla_tier="T2",
        hold_deadline=now + timedelta(minutes=30),
        weight_units=1,
        status=OrderStatus.delivered if delivered_at else OrderStatus.held,
        requested_at=requested_at or now,
        delivered_at=delivered_at,
        entry_seconds=entry_seconds,
    )
    db_session.add(order)
    await db_session.commit()
    return order


async def _delivered_webhook(db_session, client_id, order, *, seconds: float):
    endpoint = ClientWebhookEndpoint(
        client_id=client_id,
        url="https://consumer.example.com/lmx",
        secret=new_webhook_secret(),
    )
    db_session.add(endpoint)
    await db_session.commit()

    delivery = WebhookDelivery(
        endpoint_id=endpoint.id,
        order_id=order.id,
        event_id=str(uuid.uuid4()),
        payload={},
        status=DELIVERY_DELIVERED,
    )
    db_session.add(delivery)
    await db_session.commit()

    # created_at has a server default, so the span is set by moving delivered_at
    # relative to whatever it landed on rather than by inventing both.
    await db_session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery.id)
        .values(delivered_at=WebhookDelivery.created_at + timedelta(seconds=seconds))
    )
    await db_session.commit()
    return delivery


# ---------------------------------------------------------------------------
# All five metrics are accounted for
# ---------------------------------------------------------------------------


async def test_the_scorecard_covers_every_metric_in_the_plan(db_session):
    """Including the two that cannot be computed. Dropping them would make the
    scorecard look complete while quietly answering three of five."""
    scorecard = await build_scorecard(db_session)

    assert [m.name for m in scorecard.measurements] == [
        "Approval to first delivery",
        "Order entry time, second order onward",
        "Orders needing manual correction",
        "Status write-back latency",
        "Adapter changes requiring core changes",
    ]
    assert all(m.target for m in scorecard.measurements), "every metric carries its target"


async def test_an_empty_deployment_reports_no_data_rather_than_zeros(db_session):
    """**A zero would read as "we hit the target".** With no orders at all, every
    computed metric has to say it has nothing."""
    scorecard = await build_scorecard(db_session)

    for name in (
        "Approval to first delivery",
        "Order entry time",
        "Status write-back latency",
    ):
        measurement = _by_name(scorecard, name)
        assert measurement.not_measured, f"{name} invented a number from no data"
        assert measurement.median is None
        assert measurement.sample_size == 0


async def test_the_unmeasurable_metrics_explain_themselves(db_session):
    """"No data yet" and "we don't record this" are different problems: the first
    resolves with traffic, the second needs someone to build something. The text has
    to distinguish them, since it is what a reader acts on."""
    scorecard = await build_scorecard(db_session)

    correction = _by_name(scorecard, "Orders needing manual correction")
    assert "Nothing records a correction" in correction.not_measured
    # And it names what would be needed, rather than just declining.
    assert "ops action" in correction.not_measured

    coupling = _by_name(scorecard, "Adapter changes")
    assert "property of the code" in coupling.not_measured


# ---------------------------------------------------------------------------
# Order entry time
# ---------------------------------------------------------------------------


async def test_entry_time_excludes_each_clients_first_order(db_session):
    """**The exclusion is what makes this the number §3.4 targets.** A first order also
    creates the pickup shop and teaches the form, so counting it measures onboarding -
    and would make the metric look worse the more new clients we win, which is exactly
    backwards."""
    hub_id, client_id, shop_id = await _seed(db_session)
    base = datetime.now(timezone.utc) - timedelta(hours=2)

    # First order slow, the next two fast.
    await _order(db_session, hub_id, client_id, shop_id, entry_seconds=300, requested_at=base)
    await _order(
        db_session, hub_id, client_id, shop_id, entry_seconds=20,
        requested_at=base + timedelta(minutes=10),
    )
    await _order(
        db_session, hub_id, client_id, shop_id, entry_seconds=24,
        requested_at=base + timedelta(minutes=20),
    )

    entry = _by_name(await build_scorecard(db_session), "Order entry time")

    assert entry.sample_size == 2, "the first order must not be counted"
    assert entry.median == pytest.approx(22.0)


async def test_a_client_with_only_one_order_contributes_nothing(db_session):
    hub_id, client_id, shop_id = await _seed(db_session)
    await _order(db_session, hub_id, client_id, shop_id, entry_seconds=15)

    entry = _by_name(await build_scorecard(db_session), "Order entry time")

    assert entry.sample_size == 0
    assert "second order" in entry.not_measured


async def test_the_first_order_is_excluded_per_client_not_globally(db_session):
    """Two clients with two orders each should contribute two measurements, not three.
    A global ordering would drop only the very earliest order in the system."""
    hub_a, client_a, shop_a = await _seed(db_session)
    hub_b, client_b, shop_b = await _seed(db_session)
    base = datetime.now(timezone.utc) - timedelta(hours=3)

    for index, (hub, client, shop) in enumerate(
        ((hub_a, client_a, shop_a), (hub_b, client_b, shop_b))
    ):
        await _order(
            db_session, hub, client, shop, entry_seconds=100,
            requested_at=base + timedelta(minutes=index),
        )
        await _order(
            db_session, hub, client, shop, entry_seconds=10,
            requested_at=base + timedelta(hours=1, minutes=index),
        )

    entry = _by_name(await build_scorecard(db_session), "Order entry time")

    assert entry.sample_size == 2
    assert entry.median == pytest.approx(10.0)


async def test_machine_submitted_orders_are_not_counted_as_entry(db_session):
    """The public API and Epicor have nobody to time. Including them would make the
    metric appear to improve every time API volume grew."""
    hub_id, client_id, shop_id = await _seed(db_session)
    base = datetime.now(timezone.utc) - timedelta(hours=1)

    for _ in range(3):
        await _order(
            db_session, hub_id, client_id, shop_id,
            entry_seconds=1, source_system="client_api", requested_at=base,
        )

    entry = _by_name(await build_scorecard(db_session), "Order entry time")
    assert entry.sample_size == 0


async def test_the_target_is_carried_alongside_the_number(db_session):
    """So a drifting target and a drifting measurement cannot diverge in a slide."""
    entry = _by_name(await build_scorecard(db_session), "Order entry time")
    assert "30" in entry.target


# ---------------------------------------------------------------------------
# Write-back latency
# ---------------------------------------------------------------------------


async def test_write_back_latency_is_measured_from_enqueue_to_acknowledgement(db_session):
    """Exactly the span §3.4 means by "event to visible": the row is written in the
    same transaction as the status change, and delivered_at is the acknowledgement."""
    hub_id, client_id, shop_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await _delivered_webhook(db_session, client_id, order, seconds=4)
    await _delivered_webhook(db_session, client_id, order, seconds=8)

    latency = _by_name(await build_scorecard(db_session), "Status write-back latency")

    assert latency.sample_size == 2
    assert latency.median == pytest.approx(6.0)


async def test_failed_deliveries_do_not_inflate_the_latency(db_session):
    """A consumer's server being down for a day is a fact about their infrastructure,
    not about our write-back. Mixing them would make the metric unusable for the thing
    it is meant to prove."""
    hub_id, client_id, shop_id = await _seed(db_session)
    order = await _order(db_session, hub_id, client_id, shop_id)
    await _delivered_webhook(db_session, client_id, order, seconds=3)

    endpoint = ClientWebhookEndpoint(
        client_id=client_id, url="https://down.example.com/x", secret=new_webhook_secret()
    )
    db_session.add(endpoint)
    await db_session.commit()
    db_session.add(
        WebhookDelivery(
            endpoint_id=endpoint.id,
            order_id=order.id,
            event_id=str(uuid.uuid4()),
            payload={},
            status=DELIVERY_PENDING,
            attempts=4,
        )
    )
    await db_session.commit()

    latency = _by_name(await build_scorecard(db_session), "Status write-back latency")

    assert latency.sample_size == 1
    assert latency.median == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Approval to first delivery
# ---------------------------------------------------------------------------


async def test_onboarding_time_runs_from_approval_to_the_first_delivery(db_session):
    """§3.4 calls this the entire point of LMX Link. It was uncomputable until
    approval started recording when it happened."""
    approved = datetime.now(timezone.utc) - timedelta(hours=30)
    hub_id, client_id, shop_id = await _seed(db_session, approved_at=approved)

    await _order(
        db_session, hub_id, client_id, shop_id,
        delivered_at=approved + timedelta(hours=6),
    )
    # A later delivery must not move the number - the metric is the FIRST one.
    await _order(
        db_session, hub_id, client_id, shop_id,
        delivered_at=approved + timedelta(hours=20),
    )

    onboarding = _by_name(await build_scorecard(db_session), "Approval to first delivery")

    assert onboarding.unit == "hours"
    assert onboarding.sample_size == 1
    assert onboarding.median == pytest.approx(6.0)


async def test_clients_approved_before_this_was_recorded_are_excluded(db_session):
    """Estimating from `updated_at` would produce a plausible-looking number that is
    really "when someone last edited this row"."""
    hub_id, client_id, shop_id = await _seed(db_session, approved_at=None)
    await _order(
        db_session, hub_id, client_id, shop_id,
        delivered_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    onboarding = _by_name(await build_scorecard(db_session), "Approval to first delivery")

    assert onboarding.sample_size == 0
    assert onboarding.not_measured


async def test_an_approved_client_with_nothing_delivered_yet_is_excluded(db_session):
    """They are mid-onboarding, not a data point - counting them as zero hours or
    excluding them are the only options, and zero would be a lie."""
    hub_id, client_id, shop_id = await _seed(
        db_session, approved_at=datetime.now(timezone.utc) - timedelta(hours=2)
    )
    await _order(db_session, hub_id, client_id, shop_id)  # held, not delivered

    onboarding = _by_name(await build_scorecard(db_session), "Approval to first delivery")

    assert onboarding.sample_size == 0


# ---------------------------------------------------------------------------
# The plumbing that had to exist first
# ---------------------------------------------------------------------------


async def test_approving_a_signup_records_when(db_session, real_redis_client):
    """The start point of the headline metric. Approval used to flip signup_status and
    nothing else, so the instant was lost."""
    from app.api.admin_routes import approve_signup
    from app.models.hub import Hub as HubModel
    from app.ops_auth.dependencies import AuthedOpsUser
    from app.schemas.signup import ApproveSignupBody

    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(HubModel(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id,
            hub_id=hub_id,
            name="Applicant Co",
            pos_system="client_portal",
            signup_status="pending",
        )
    )
    await db_session.commit()

    await approve_signup(
        str(client_id),
        ApproveSignupBody(rates=[{"sla_tier": "T2", "rate_per_drop_cents": 1500}]),
        session=db_session,
        _admin=AuthedOpsUser(
            ops_user_id=str(uuid.uuid4()), email="ops@lmxit.com", name="Ops", role="admin"
        ),
    )

    client = await db_session.get(Client, client_id)
    await db_session.refresh(client)
    assert client.approved_at is not None


async def test_a_portal_order_persists_its_entry_time(db_session, real_redis_client):
    """It was logged and never stored, so the distribution §3.4 targets was
    unrecoverable without building log aggregation for a number we already had."""
    from app.batch_queue.store import HoldQueueStore
    from app.geocoding.base import BaseGeocoder, GeocodeResult
    from app.ingestion.service import ingest_lmx_order
    from app.schemas.lmx_order import LMXOrder

    class _Geo(BaseGeocoder):
        provider_name = "fake"

        async def geocode(self, address):
            return GeocodeResult(lat=30.26, lng=-97.74, display_name=address, provider="fake")

    hub_id, client_id, _shop_id = await _seed(db_session)

    order = await ingest_lmx_order(
        db_session,
        HoldQueueStore(),
        LMXOrder(
            source_system="client_portal",
            source_order_ref=f"REF-{uuid.uuid4().hex[:8]}",
            hub_id=str(hub_id),
            client_id=str(client_id),
            pickup_address="1200 E 6th St, Austin TX",
            drop_address_raw="900 Congress Ave, Austin TX",
            drop_lat=30.27,
            drop_lng=-97.75,
            entry_seconds=18,
            received_at=datetime.now(timezone.utc),
        ),
        geocoder=_Geo(),
    )

    assert order.entry_seconds == 18
