"""REC-3: outcomes attach by key, and the decision stays exactly as written.

The done-when is a structural claim, so the central test is structural: record
a decision, attach outcomes to it, and check the decision row has not moved a
byte. It passes trivially today because 0051's trigger makes the alternative
impossible - and that is the point of having it. The test is what notices if
somebody later makes the decision table writable "just for this one field".
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.models.client import Client
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.outcome_entry import (
    KIND_DELIVERED,
    KIND_DWELL,
    SUBJECT_ORDER,
    OutcomeEntry,
)
from app.record import (
    current_outcome,
    outcomes_for,
    record_decision,
    record_delivery_outcome,
    record_outcome,
    supersede_outcome,
)
from app.schemas.optimizer import CyclePlan
from app.sla.commitment import Commitment

pytestmark = pytest.mark.integration

PROMISED = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


async def _hub_and_client(db_session):
    hub = Hub(id=uuid.uuid4(), name="Outcome Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Outcome Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    return hub, client


async def _delivered_order(db_session, hub, client, *, delivered_at) -> Order:
    order = Order(
        hub_id=hub.id, client_id=client.id, external_order_ref=f"PO-{uuid.uuid4().hex[:6]}",
        source_system="flat_file", raw_payload={}, sla_tier="T2",
        status=OrderStatus.delivered, requested_at=PROMISED - timedelta(hours=2),
        delivered_at=delivered_at,
    )
    db_session.add(order)
    await db_session.flush()
    return order


def _empty_plan(hub_id: str) -> CyclePlan:
    return CyclePlan(
        hub_id=hub_id, planned_at=PROMISED - timedelta(hours=1), hub_closed=False,
        held_order_count=0, released_order_ids=[], shop_name_by_order_id={},
        fleet_snapshot=[], stops=[], drivers=[], assignments=[], unassigned_stop_ids=[],
        engine="stub_nearest_neighbor", plan_duration_seconds=0.01,
    )


class TestAttachingDoesNotTouchTheDecision:
    async def test_the_decision_row_is_unchanged_after_outcomes_attach(self, db_session):
        """REC-3's done-when, checked rather than assumed."""
        hub, client = await _hub_and_client(db_session)
        decision = await record_decision(db_session, _empty_plan(str(hub.id)))
        await db_session.commit()

        before = (
            await db_session.execute(
                text(
                    "SELECT inputs_hash, assignments::text, decided_at, engine "
                    "FROM decision_snapshots WHERE id = :id"
                ),
                {"id": decision.id},
            )
        ).one()

        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)
        await record_delivery_outcome(
            db_session,
            order,
            Commitment(promised_delivery_by=PROMISED, source="lmx"),
            decision_snapshot_id=decision.id,
        )
        await record_outcome(
            db_session, hub_id=hub.id, subject_type=SUBJECT_ORDER, subject_id=order.id,
            kind=KIND_DWELL, occurred_at=PROMISED, values={"dwell_seconds": 42},
            decision_snapshot_id=decision.id,
        )
        await db_session.commit()

        after = (
            await db_session.execute(
                text(
                    "SELECT inputs_hash, assignments::text, decided_at, engine "
                    "FROM decision_snapshots WHERE id = :id"
                ),
                {"id": decision.id},
            )
        ).one()
        assert before == after

    async def test_outcomes_are_found_by_the_decision_that_caused_them(self, db_session):
        hub, client = await _hub_and_client(db_session)
        decision = await record_decision(db_session, _empty_plan(str(hub.id)))
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)
        await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx"),
            decision_snapshot_id=decision.id,
        )

        attached = list(
            await db_session.scalars(
                select(OutcomeEntry).where(
                    OutcomeEntry.decision_snapshot_id == decision.id
                )
            )
        )
        assert len(attached) == 1
        assert attached[0].subject_id == order.id

    async def test_an_outcome_with_no_decision_behind_it_is_still_recorded(self, db_session):
        """A delivery a human dispatched, or one made before REC-1 existed.

        Refusing these would bias every later measurement towards exactly the
        deliveries the system handled - which is the population whose
        performance is being claimed.
        """
        hub, client = await _hub_and_client(db_session)
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)

        entry = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="external")
        )
        assert entry.decision_snapshot_id is None


class TestDeliveryOutcomes:
    async def test_lateness_is_signed_so_early_is_visible(self, db_session):
        hub, client = await _hub_and_client(db_session)
        early = await _delivered_order(
            db_session, hub, client, delivered_at=PROMISED - timedelta(minutes=12)
        )

        entry = await record_delivery_outcome(
            db_session, early, Commitment(promised_delivery_by=PROMISED, source="lmx")
        )

        assert entry.values["lateness_seconds"] == -720
        assert entry.values["on_time"] is True

    async def test_a_late_delivery_records_by_how_much(self, db_session):
        hub, client = await _hub_and_client(db_session)
        late = await _delivered_order(
            db_session, hub, client, delivered_at=PROMISED + timedelta(minutes=9)
        )

        entry = await record_delivery_outcome(
            db_session, late, Commitment(promised_delivery_by=PROMISED, source="lmx")
        )

        assert entry.values["lateness_seconds"] == 540
        assert entry.values["on_time"] is False

    async def test_no_promise_records_null_rather_than_on_time(self, db_session):
        """Counting an unpromised delivery as a success would flatter every
        on-time figure that used it."""
        hub, client = await _hub_and_client(db_session)
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)

        entry = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=None, source="none")
        )

        assert entry.values["on_time"] is None
        assert entry.values["lateness_seconds"] is None

    async def test_whose_promise_it_was_is_recorded(self, db_session):
        """An LMX commitment missed is a breach; an EXTERNAL window missed is
        somebody else's promise. Summing them into one on-time rate would
        describe neither (§1.1's sla_owner split)."""
        hub, client = await _hub_and_client(db_session)
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)

        entry = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="external")
        )
        assert entry.values["commitment_source"] == "external"

    async def test_the_promise_is_stored_not_recomputed(self, db_session):
        """SLA terms change. An outcome recomputed later would judge a delivery
        by rules that did not apply to it, quietly, in whichever direction
        favoured whoever changed the terms."""
        hub, client = await _hub_and_client(db_session)
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)

        entry = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx")
        )
        assert entry.values["promised_delivery_by"] == PROMISED.isoformat()

    async def test_an_undelivered_order_is_refused(self, db_session):
        hub, client = await _hub_and_client(db_session)
        order = Order(
            hub_id=hub.id, client_id=client.id, external_order_ref="PO-X",
            source_system="flat_file", raw_payload={}, sla_tier="T2",
            status=OrderStatus.held, requested_at=PROMISED,
        )
        db_session.add(order)
        await db_session.flush()

        with pytest.raises(ValueError, match="not delivered"):
            await record_delivery_outcome(
                db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx")
            )


class TestCorrections:
    async def test_a_correction_is_a_new_entry_and_the_old_one_survives(self, db_session):
        hub, client = await _hub_and_client(db_session)
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)
        first = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx")
        )

        corrected = await supersede_outcome(
            db_session, first,
            values={"on_time": False, "lateness_seconds": 1800},
            reason="Recipient confirmed it arrived half an hour after the tap",
        )

        history = await outcomes_for(db_session, subject_id=order.id)
        assert len(history) == 2, "the original must survive"
        assert corrected.supersedes == first.id
        assert "half an hour" in corrected.values["correction_reason"]

    async def test_the_current_outcome_is_the_one_not_superseded(self, db_session):
        hub, client = await _hub_and_client(db_session)
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)
        first = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx")
        )
        corrected = await supersede_outcome(
            db_session, first, values={"on_time": False}, reason="Disputed and upheld"
        )

        live = await current_outcome(db_session, subject_id=order.id, kind=KIND_DELIVERED)
        assert live.id == corrected.id

    async def test_a_correction_without_a_reason_is_refused(self, db_session):
        """A changed number with no stated cause is indistinguishable from a
        mistake, and why it changed is the first thing anybody asks."""
        hub, client = await _hub_and_client(db_session)
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)
        first = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx")
        )

        with pytest.raises(ValueError, match="needs a reason"):
            await supersede_outcome(db_session, first, values={}, reason="   ")


class TestTheLedgerIsALedger:
    async def test_the_database_refuses_to_update_an_entry(self, db_session):
        hub, client = await _hub_and_client(db_session)
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)
        entry = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx")
        )
        await db_session.commit()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text("UPDATE outcome_ledger SET kind = 'failed' WHERE id = :id"),
                {"id": entry.id},
            )
        await db_session.rollback()

    async def test_the_database_refuses_to_delete_an_entry(self, db_session):
        hub, client = await _hub_and_client(db_session)
        order = await _delivered_order(db_session, hub, client, delivered_at=PROMISED)
        entry = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx")
        )
        await db_session.commit()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text("DELETE FROM outcome_ledger WHERE id = :id"), {"id": entry.id}
            )
        await db_session.rollback()

    async def test_an_unknown_kind_is_refused_before_it_reaches_the_database(self, db_session):
        hub, _ = await _hub_and_client(db_session)
        with pytest.raises(ValueError, match="kind must be"):
            await record_outcome(
                db_session, hub_id=hub.id, subject_type=SUBJECT_ORDER,
                subject_id=uuid.uuid4(), kind="vibes", occurred_at=PROMISED,
            )

    async def test_recorded_at_and_occurred_at_are_separate(self, db_session):
        """An outbox flush or a dispute makes the gap large, and a measurement
        that assumed they were the same would place a delivery on the wrong
        day."""
        hub, client = await _hub_and_client(db_session)
        happened = PROMISED - timedelta(days=2)
        order = await _delivered_order(db_session, hub, client, delivered_at=happened)

        entry = await record_delivery_outcome(
            db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx")
        )

        assert entry.occurred_at == happened
        assert entry.recorded_at > happened
