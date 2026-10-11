"""ING-2: a replayed order is one record and one billable event, under either fee unit.

The fee unit is decision 0.5 and still open, so the billing event is configuration
(`settings.billing_event`, decision 2.2(d)): an order once delivered, or an order
once taken in. Each test runs under both.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.batch_queue.store import HoldQueueStore
from app.billing.reconciliation import reconcile_period
from app.billing.service import NoBillableOrdersError, generate_invoice
from app.config import settings
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.ingestion.service import ingest_or_find
from app.models.client import Client
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.schemas.lmx_order import LMXOrder

pytestmark = pytest.mark.integration

EVENTS = ("order_delivered", "order_ingested")
TAKEN_IN = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
SEPTEMBER = (date(2026, 9, 1), date(2026, 10, 1))


class FakeGeocoder(BaseGeocoder):
    provider_name = "fake"

    async def geocode(self, address: str) -> GeocodeResult | None:
        return GeocodeResult(lat=30.2646, lng=-97.7302, display_name=address, provider="fake")


async def _client(db_session) -> Client:
    hub = Hub(id=uuid.uuid4(), name="Billing Hub", lat=30.267, lng=-97.743, timezone="UTC")
    db_session.add(hub)
    await db_session.commit()
    client = Client(hub_id=hub.id, name="Design Partner", pos_system="flat_file")
    db_session.add(client)
    await db_session.commit()
    return client


def _lmx(client: Client, ref: str) -> LMXOrder:
    return LMXOrder(
        source_system="flat_file", source_order_ref=ref, hub_id=str(client.hub_id), client_id=str(client.id),
        pickup_address="1200 E 6th St, Austin TX", drop_address_raw="900 Congress Ave, Austin TX",
        drop_lat=30.2729, drop_lng=-97.7414, received_at=TAKEN_IN,
    )


async def _ingest(db_session, client, ref, *, delivered: bool):
    ingested = await ingest_or_find(db_session, HoldQueueStore(), _lmx(client, ref), geocoder=FakeGeocoder())
    order = ingested.order
    order.fee_cents = 1500
    if delivered:
        order.status = OrderStatus.delivered
        order.delivered_at = TAKEN_IN + timedelta(hours=2)
    else:
        order.status = OrderStatus.cancelled
    await db_session.commit()
    return ingested


@pytest.mark.parametrize("event", EVENTS)
async def test_a_replayed_order_is_one_record_and_one_billable_event(
    db_session, real_redis_client, monkeypatch, event
):
    monkeypatch.setattr(settings, "billing_event", event)
    client = await _client(db_session)

    first = await _ingest(db_session, client, "PO-1001", delivered=True)
    replay = await ingest_or_find(db_session, HoldQueueStore(), _lmx(client, "PO-1001"), geocoder=FakeGeocoder())

    assert (first.replayed, replay.replayed) == (False, True)
    assert replay.order.id == first.order.id
    assert await db_session.scalar(select(func.count()).select_from(Order)) == 1

    invoice = await generate_invoice(db_session, client.id, *SEPTEMBER)
    assert invoice.gross_cents == 1500
    # The billable event happened once: the period has nothing left to bill.
    with pytest.raises(NoBillableOrdersError):
        await generate_invoice(db_session, client.id, *SEPTEMBER)


@pytest.mark.parametrize(("event", "billed"), [("order_delivered", 1500), ("order_ingested", 3000)])
async def test_the_key_decides_whether_an_undelivered_order_is_billed(
    db_session, real_redis_client, monkeypatch, event, billed
):
    monkeypatch.setattr(settings, "billing_event", event)
    client = await _client(db_session)
    await _ingest(db_session, client, "PO-1", delivered=True)
    await _ingest(db_session, client, "PO-2", delivered=False)

    invoice = await generate_invoice(db_session, client.id, *SEPTEMBER)

    assert invoice.gross_cents == billed


@pytest.mark.parametrize("event", EVENTS)
async def test_the_month_end_reconciliation_follows_the_key(db_session, real_redis_client, monkeypatch, event):
    monkeypatch.setattr(settings, "billing_event", event)
    client = await _client(db_session)
    await _ingest(db_session, client, "PO-1", delivered=True)
    await _ingest(db_session, client, "PO-2", delivered=False)
    await generate_invoice(db_session, client.id, *SEPTEMBER)

    report = await reconcile_period(
        db_session, client_id=client.id,
        since=datetime(2026, 9, 1, tzinfo=timezone.utc), until=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )

    assert report.summary()["billed_on"] == event.replace("_", " ")
    # Under the ingest key the cancelled order was billed, so the period
    # reconciles; under the delivery key it is the gap the report exists to show.
    assert report.reconciles is (event == "order_ingested")
