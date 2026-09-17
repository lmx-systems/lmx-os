"""STL-3: does everything we ingested reach an invoice?

The interesting test is `TestTheUnitMismatch`. The fee unit is per order
ingested and the invoice bills per order delivered, so a failed delivery is free
today - and that is a live commercial question rather than a defect, which means
the job here is to count it rather than to quietly reconcile it away.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.billing.reconciliation import (
    BILLED_ON,
    FEE_UNIT,
    _STILL_MOVING,
    reconcile_period,
    render,
)
from app.models.client import Client
from app.models.hub import Hub
from app.models.invoice import Invoice
from app.models.order import Order, OrderStatus

pytestmark = pytest.mark.integration

SINCE = datetime(2026, 8, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 9, 1, tzinfo=timezone.utc)
MID = datetime(2026, 8, 15, tzinfo=timezone.utc)


async def _client(db_session) -> Client:
    hub = Hub(id=uuid.uuid4(), name="Recon Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(
        id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file"
    )
    db_session.add(client)
    await db_session.flush()
    return client


async def _order(
    db_session, client, *, status=OrderStatus.delivered, fee_cents=1200,
    invoiced=False, requested_at=MID,
) -> Order:
    order = Order(
        hub_id=client.hub_id, client_id=client.id,
        external_order_ref=f"PO-{uuid.uuid4().hex[:8]}", source_system="flat_file",
        raw_payload={}, sla_tier="T2", status=status, requested_at=requested_at,
        fee_cents=fee_cents,
        delivered_at=MID if status == OrderStatus.delivered else None,
    )
    db_session.add(order)
    await db_session.flush()
    if invoiced:
        invoice = Invoice(
            client_id=client.id, period_start=SINCE.date(), period_end=UNTIL.date(),
            gross_cents=fee_cents or 0, credit_cents=0, total_cents=fee_cents or 0,
        )
        db_session.add(invoice)
        await db_session.flush()
        order.invoice_id = invoice.id
        await db_session.flush()
    return order


async def _reconcile(db_session, client):
    return await reconcile_period(
        db_session, client_id=client.id, since=SINCE, until=UNTIL
    )


class TestACleanPeriod:
    async def test_everything_billed_reconciles(self, db_session):
        client = await _client(db_session)
        for _ in range(3):
            await _order(db_session, client, invoiced=True)
        report = await _reconcile(db_session, client)
        assert report.ingested == 3
        assert report.billed == 3
        assert report.reconciles is True
        assert "accounted for" in render(report)

    async def test_an_order_still_moving_is_not_a_discrepancy(self, db_session):
        """Crying wolf every month end for orders that are simply in flight
        would make the report worth ignoring."""
        client = await _client(db_session)
        await _order(db_session, client, status=OrderStatus.en_route_drop)
        report = await _reconcile(db_session, client)
        assert report.in_flight == 1
        assert report.reconciles is True

    async def test_the_moving_set_is_derived_not_listed(self):
        """A hand-written list silently misclassifies any status added later -
        a new one would land in 'never delivered' and read as lost revenue.
        `en_route_drop` was missing from the first draft of exactly such a list."""
        assert OrderStatus.en_route_drop in _STILL_MOVING
        assert OrderStatus.delivered not in _STILL_MOVING
        assert len(_STILL_MOVING) + 4 == len(list(OrderStatus))


class TestMoneyEarnedAndNotAskedFor:
    async def test_a_delivered_priced_order_on_no_invoice_is_flagged(self, db_session):
        client = await _client(db_session)
        await _order(db_session, client, invoiced=False)
        report = await _reconcile(db_session, client)
        assert len(report.delivered_not_invoiced) == 1
        assert report.reconciles is False
        assert "never asked for" in render(report)

    async def test_an_unpriced_order_is_its_own_category(self, db_session):
        """`generate_invoice` excludes these and logs a warning. A log line is
        not a control - nobody reads warnings from three weeks ago."""
        client = await _client(db_session)
        await _order(db_session, client, fee_cents=None)
        report = await _reconcile(db_session, client)
        assert len(report.unpriced) == 1
        assert report.delivered_not_invoiced == []
        assert "no rate configured" in render(report)


class TestTheUnitMismatch:
    async def test_a_failed_delivery_is_counted_not_reconciled_away(self, db_session):
        """The fee unit is per order ingested; the invoice bills per order
        delivered. A failed delivery is free today. That is a commercial
        question with an owner, so this counts it rather than deciding it."""
        client = await _client(db_session)
        await _order(db_session, client, status=OrderStatus.delivery_failed)
        await _order(db_session, client, status=OrderStatus.cancelled)
        report = await _reconcile(db_session, client)
        assert len(report.not_delivered) == 2
        assert report.reconciles is False

    async def test_the_report_names_both_units(self, db_session):
        client = await _client(db_session)
        await _order(db_session, client, status=OrderStatus.cancelled)
        text = render(report := await _reconcile(db_session, client))
        assert FEE_UNIT in text
        assert BILLED_ON in text
        assert report.summary()["fee_unit"] == "order ingested"
        assert report.summary()["billed_on"] == "order delivered"

    async def test_it_does_not_pretend_the_gap_is_fine(self, db_session):
        """`reconciles` is False with unbilled non-deliveries on purpose. Under
        the stated unit those orders are billable, so the period does not
        reconcile - it reveals the mismatch, which is the point."""
        client = await _client(db_session)
        await _order(db_session, client, invoiced=True)
        await _order(db_session, client, status=OrderStatus.returned)
        assert (await _reconcile(db_session, client)).reconciles is False


class TestItIsWindowedOnIngestion:
    async def test_an_order_ingested_outside_the_window_is_not_counted(self, db_session):
        client = await _client(db_session)
        await _order(db_session, client, requested_at=SINCE - timedelta(days=2))
        report = await _reconcile(db_session, client)
        assert report.ingested == 0

    async def test_an_order_ingested_late_and_delivered_next_month_is_visible(
        self, db_session
    ):
        """The mismatch only a check anchored to the ingest can see: billing
        windows on delivered_at, so this order is billed in a different period
        from the one it was taken in."""
        client = await _client(db_session)
        order = await _order(
            db_session, client, requested_at=UNTIL - timedelta(hours=2), invoiced=False
        )
        order.delivered_at = UNTIL + timedelta(days=1)
        await db_session.flush()
        report = await _reconcile(db_session, client)
        assert report.ingested == 1
        assert len(report.delivered_not_invoiced) == 1

    async def test_another_client_is_not_counted(self, db_session):
        client = await _client(db_session)
        other = await _client(db_session)
        await _order(db_session, other, invoiced=False)
        assert (await _reconcile(db_session, client)).ingested == 0
