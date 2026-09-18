"""ING-3: history re-ingests without double-counting or mutating decisions.

Two clauses, and each fails in a way the other's tests would not catch.

**Double-counting** is what happens when an import is priced. `generate_invoice`
selects delivered orders with a non-null `fee_cents` and no invoice, and a
backfilled historical delivery matches every clause of that - so a customer is
billed a second time for work already invoiced, by an import rather than by
anything anyone did wrong on the day.

**Mutating decisions** is the quieter one, and a unique constraint does not give
it to you. The constraint stops the second row; it says nothing about the first.
A replay that refreshed `requested_at`, re-priced, or moved a status would leave
one row - correctly - and change the facts a settled statement and a recorded
cycle were built on.

The permanent one has a class of its own. `experiment_assignments` is append-only
and immutable by trigger, so an arm assigned to an order delivered three weeks
ago cannot be removed afterwards.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.batch_queue.store import HoldQueueStore
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.ingestion.backfill import BackfillReport, backfill_orders
from app.ingestion.service import ingest_lmx_order
from app.models.client import Client
from app.models.experiment_assignment import ExperimentAssignment
from app.models.hub import Hub
from app.models.order import (
    INTAKE_BACKFILL,
    INTAKE_LIVE,
    Order,
    OrderStatus,
)
from app.schemas.lmx_order import LMXOrder

pytestmark = pytest.mark.integration

PICKUP_ADDRESS = "1200 E 6th St, Austin TX"
PICKUP_LAT, PICKUP_LNG = 30.2646, -97.7302

CONTRACTED = datetime(2026, 9, 1, tzinfo=timezone.utc)
LONG_AGO = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)


class FakeGeocoder(BaseGeocoder):
    provider_name = "fake"

    async def geocode(self, address: str) -> GeocodeResult | None:
        return GeocodeResult(
            lat=PICKUP_LAT, lng=PICKUP_LNG, display_name=address, provider="fake"
        )


async def _seed(db_session, *, control_arm=False):
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Backfill Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id,
            hub_id=hub_id,
            name="Design Partner",
            pos_system="flat_file",
            control_arm_fraction=0.10 if control_arm else None,
            control_arm_contracted_at=CONTRACTED if control_arm else None,
        )
    )
    await db_session.commit()
    return hub_id, client_id


def _order(hub_id, client_id, *, ref=None, received_at=None, **overrides) -> LMXOrder:
    payload = dict(
        source_system="dispatch_export",
        source_order_ref=ref or f"HIST-{uuid.uuid4().hex[:8]}",
        hub_id=str(hub_id),
        client_id=str(client_id),
        pickup_address=PICKUP_ADDRESS,
        drop_address_raw="900 Congress Ave, Austin TX",
        drop_lat=30.2729,
        drop_lng=-97.7414,
        received_at=received_at or LONG_AGO,
    )
    payload.update(overrides)
    return LMXOrder(**payload)


class TestHistoryIsNotBilledTwice:
    async def test_a_backfilled_order_is_never_priced(self, db_session, real_redis_client):
        """The double-count, at its source. A priced historical delivery is
        indistinguishable from an unbilled live one to `generate_invoice`."""
        hub_id, client_id = await _seed(db_session)

        report = await backfill_orders(
            db_session, [_order(hub_id, client_id)], geocoder=FakeGeocoder()
        )

        assert report.created == 1
        order = (await db_session.scalars(select(Order))).one()
        assert order.intake_mode == INTAKE_BACKFILL
        assert order.fee_cents is None

    async def test_the_invoice_query_excludes_it_as_well(self, db_session, real_redis_client):
        """The second guard, independent of the first. If a future change prices
        a backfill - or an import writes rows directly - the customer must still
        not be billed for it."""
        from app.billing.service import generate_invoice

        hub_id, client_id = await _seed(db_session)
        await backfill_orders(
            db_session, [_order(hub_id, client_id)], geocoder=FakeGeocoder()
        )
        order = (await db_session.scalars(select(Order))).one()

        # Force the state the first guard prevents: delivered, priced, unbilled.
        order.status = OrderStatus.delivered
        order.delivered_at = LONG_AGO + timedelta(hours=2)
        order.fee_cents = 1500
        await db_session.commit()

        from app.billing.service import NoBillableOrdersError

        with pytest.raises(NoBillableOrdersError):
            await generate_invoice(
                db_session,
                client_id=client_id,
                period_start=LONG_AGO.date(),
                period_end=(LONG_AGO + timedelta(days=30)).date(),
            )

    async def test_a_live_order_in_the_same_period_is_still_billed(
        self, db_session, real_redis_client
    ):
        """The exclusion must not be a filter that quietly drops live work too -
        which is the way a guard like this usually fails."""
        from app.billing.service import generate_invoice

        hub_id, client_id = await _seed(db_session)
        live = await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FakeGeocoder()
        )
        live.status = OrderStatus.delivered
        live.delivered_at = LONG_AGO + timedelta(hours=2)
        live.fee_cents = 1500
        await db_session.commit()

        invoice = await generate_invoice(
            db_session,
            client_id=client_id,
            period_start=LONG_AGO.date(),
            period_end=(LONG_AGO + timedelta(days=30)).date(),
        )

        assert invoice is not None and invoice.total_cents == 1500


class TestHistoryIsNotPutInAnExperiment:
    async def test_a_backfill_is_never_assigned_a_control_arm(
        self, db_session, real_redis_client
    ):
        """The permanent one. `experiment_assignments` is append-only and
        immutable by trigger, so an arm on an order delivered three weeks ago
        cannot be removed - an import would contaminate EXP-1's measurement for
        good, and the contamination would look like data."""
        hub_id, client_id = await _seed(db_session, control_arm=True)

        await backfill_orders(
            db_session,
            [_order(hub_id, client_id) for _ in range(20)],
            geocoder=FakeGeocoder(),
        )

        count = await db_session.scalar(
            select(func.count()).select_from(ExperimentAssignment)
        )
        assert count == 0, "history was enrolled in a live experiment"

    async def test_a_live_order_for_the_same_client_still_gets_one(
        self, db_session, real_redis_client
    ):
        """Proves the suppression is about the mode and not about the client
        being misconfigured - otherwise the test above passes for the wrong
        reason and EXP-1 is silently off."""
        hub_id, client_id = await _seed(db_session, control_arm=True)

        for _ in range(20):
            await ingest_lmx_order(
                db_session, HoldQueueStore(), _order(hub_id, client_id),
                geocoder=FakeGeocoder(),
            )

        count = await db_session.scalar(
            select(func.count()).select_from(ExperimentAssignment)
        )
        assert count == 20


class TestHistoryIsNotDispatched:
    async def test_a_backfill_never_reaches_the_hold_queue(
        self, db_session, real_redis_client
    ):
        """`backfill_orders` passes a hold queue that raises if used. A history
        import landing in Redis sends a driver to collect a delivery that
        happened three weeks ago - the only one of these failures anybody would
        notice within the hour."""
        hub_id, client_id = await _seed(db_session)

        report = await backfill_orders(
            db_session, [_order(hub_id, client_id)], geocoder=FakeGeocoder()
        )

        assert report.created == 1
        assert report.failed == 0

    async def test_the_queue_guard_is_real(self, db_session):
        """Asserts the belt exists rather than trusting the test above, which
        would also pass if the guard were a no-op."""
        from app.ingestion.backfill import _RefusingHoldQueue

        with pytest.raises(AssertionError, match="history must never be dispatched"):
            await _RefusingHoldQueue().add("hub", None)


class TestReplayDoesNotMutateDecisions:
    async def test_re_ingesting_returns_the_same_order(self, db_session, real_redis_client):
        hub_id, client_id = await _seed(db_session)
        lmx = _order(hub_id, client_id, ref="STABLE-1")

        first = await ingest_lmx_order(
            db_session, HoldQueueStore(), lmx, geocoder=FakeGeocoder()
        )
        second = await ingest_lmx_order(
            db_session, HoldQueueStore(), lmx, geocoder=FakeGeocoder()
        )

        assert first.id == second.id
        assert await db_session.scalar(select(func.count()).select_from(Order)) == 1

    async def test_a_replay_leaves_every_field_alone(self, db_session, real_redis_client):
        """The clause a unique constraint does not give you. One row is the
        constraint's job; that row being unchanged is this one's - and by the
        time a replay happens, a cycle may have decided something about this
        order and a statement may have been settled on it."""
        hub_id, client_id = await _seed(db_session)
        lmx = _order(hub_id, client_id, ref="STABLE-2")

        original = await ingest_lmx_order(
            db_session, HoldQueueStore(), lmx, geocoder=FakeGeocoder()
        )
        # Everything a live order accumulates after intake.
        original.status = OrderStatus.delivered
        original.delivered_at = LONG_AGO + timedelta(hours=3)
        original.fee_cents = 2200
        await db_session.commit()
        before = {
            "requested_at": original.requested_at,
            "status": original.status,
            "delivered_at": original.delivered_at,
            "fee_cents": original.fee_cents,
            "sla_tier": original.sla_tier,
            "hold_deadline": original.hold_deadline,
        }

        await ingest_lmx_order(
            db_session,
            HoldQueueStore(),
            _order(hub_id, client_id, ref="STABLE-2", received_at=datetime.now(timezone.utc)),
            geocoder=FakeGeocoder(),
        )
        await db_session.refresh(original)

        after = {
            "requested_at": original.requested_at,
            "status": original.status,
            "delivered_at": original.delivered_at,
            "fee_cents": original.fee_cents,
            "sla_tier": original.sla_tier,
            "hold_deadline": original.hold_deadline,
        }
        assert after == before, "a replay changed a decided order"

    async def test_a_replayed_backfill_does_not_create_a_second_row(
        self, db_session, real_redis_client
    ):
        """Re-running an import is the normal way to finish one that failed
        halfway. It has to be the cheap thing to do."""
        hub_id, client_id = await _seed(db_session)
        orders = [_order(hub_id, client_id, ref=f"HIST-{i}") for i in range(5)]

        first = await backfill_orders(db_session, orders, geocoder=FakeGeocoder())
        second = await backfill_orders(db_session, orders, geocoder=FakeGeocoder())

        assert (first.created, first.replayed) == (5, 0)
        assert (second.created, second.replayed) == (0, 5)
        assert await db_session.scalar(select(func.count()).select_from(Order)) == 5

    async def test_two_clients_may_use_the_same_reference(
        self, db_session, real_redis_client
    ):
        """Two customers' ERPs both number their orders from 1. Idempotency
        scoped globally makes one of them unable to send us their second order -
        the bug migration 0035 fixed, re-asserted here because `find_existing_order`
        is a second place that has to agree with the index."""
        hub_id, one = await _seed(db_session)
        two = uuid.uuid4()
        db_session.add(Client(id=two, hub_id=hub_id, name="Other", pos_system="flat_file"))
        await db_session.commit()

        await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, one, ref="1"),
            geocoder=FakeGeocoder(),
        )
        await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, two, ref="1"),
            geocoder=FakeGeocoder(),
        )

        assert await db_session.scalar(select(func.count()).select_from(Order)) == 2

    async def test_every_order_has_something_to_be_idempotent_on(self):
        """Replay is a property of the contract, not a best effort.

        `source_order_ref` is required with `min_length=1`, so a source that
        cannot identify its own orders cannot reach intake - which is why
        `find_existing_order` has no "we could not tell" branch to get wrong.
        If this ever becomes optional, replay silently stops working for that
        source and the failure is a duplicate rather than an error.
        """
        from pydantic import ValidationError

        field = LMXOrder.model_fields["source_order_ref"]
        assert field.is_required()
        assert any(getattr(m, "min_length", None) == 1 for m in field.metadata)

        with pytest.raises(ValidationError):
            _order(uuid.uuid4(), uuid.uuid4(), source_order_ref="")


class TestTheReportIsHonest:
    async def test_replayed_is_not_folded_into_created(self):
        """A run reporting "1,400 imported" when 1,380 were already there is the
        report that hides a double-count rather than ruling one out."""
        report = BackfillReport(created=20, replayed=1380)

        assert report.considered == 1400
        assert "20 imported" in report.summary()
        assert "1380 already present" in report.summary()

    async def test_a_rerun_with_nothing_new_says_so(self):
        report = BackfillReport(created=0, replayed=5)
        assert "had already been run" in report.summary()

    async def test_a_failure_names_the_row_to_look_at(self, db_session, real_redis_client):
        hub_id, client_id = await _seed(db_session)

        class FailingGeocoder(BaseGeocoder):
            provider_name = "fake"

            async def geocode(self, address: str) -> GeocodeResult | None:
                return None

        report = await backfill_orders(
            db_session,
            [_order(hub_id, client_id, ref="BAD-1")],
            geocoder=FailingGeocoder(),
        )

        assert report.failed == 1
        assert report.failures[0][0] == "BAD-1"

    async def test_one_bad_row_does_not_lose_the_rest(self, db_session, real_redis_client):
        """A 20,000-row import that rolls back entirely because row 19,000 has an
        address nobody can geocode is an import that never completes."""
        hub_id, client_id = await _seed(db_session)

        calls = {"n": 0}

        class FlakyGeocoder(BaseGeocoder):
            provider_name = "fake"

            async def geocode(self, address: str) -> GeocodeResult | None:
                calls["n"] += 1
                if calls["n"] == 2:
                    return None
                return GeocodeResult(
                    lat=PICKUP_LAT, lng=PICKUP_LNG, display_name=address, provider="fake"
                )

        report = await backfill_orders(
            db_session,
            [
                _order(hub_id, client_id, ref=f"MIX-{i}", pickup_address=f"{i} Test St, Austin TX")
                for i in range(3)
            ],
            geocoder=FlakyGeocoder(),
        )

        assert report.failed == 1
        assert report.created == 2


class TestTheModeIsOnTheRow:
    async def test_live_intake_is_still_the_default(self, db_session, real_redis_client):
        hub_id, client_id = await _seed(db_session)

        order = await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FakeGeocoder()
        )

        assert order.intake_mode == INTAKE_LIVE

    async def test_an_unknown_mode_is_refused(self, db_session, real_redis_client):
        hub_id, client_id = await _seed(db_session)

        with pytest.raises(ValueError, match="mode must be"):
            await ingest_lmx_order(
                db_session, HoldQueueStore(), _order(hub_id, client_id),
                geocoder=FakeGeocoder(), mode="historical",
            )

    async def test_the_database_refuses_a_mode_outside_the_two(
        self, db_session, real_redis_client
    ):
        from sqlalchemy import text

        hub_id, client_id = await _seed(db_session)
        order = await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FakeGeocoder()
        )
        await db_session.commit()

        with pytest.raises(Exception, match="ck_orders_intake_mode"):
            await db_session.execute(
                text("UPDATE orders SET intake_mode = 'archived' WHERE id = :id"),
                {"id": order.id},
            )
        await db_session.rollback()
