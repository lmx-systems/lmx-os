"""The arm at intake, and the abstention that proves we honoured it.

`EXP-1`'s done-when says the arm is assigned **at intake** - an arm chosen any
later can be chosen knowing something about the order. This is that wiring, plus
the thing `EXP-3` had to report as unverifiable until now: a control order is
dispatched as the customer would have, so our batching hold does not apply, and
declining to take it gets written down.

Inert for every client today. The gate is a recorded contract date, not a flag.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.batch_queue.store import HoldQueueStore
from app.experiment.arms import _draw, block_size
from app.experiment.exclusions import exclude_receiver
from app.ingestion.service import ingest_order
from app.models.client import Client
from app.models.experiment_assignment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    EXPERIMENT_CONTROL_ARM,
    ExperimentAssignment,
)
from app.models.hub import Hub
from app.models.outcome_entry import KIND_ARM_ABSTENTION
from app.models.shop import Shop
from app.record.outcomes import outcomes_for
from sqlalchemy import select

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
CONTRACTED = datetime(2026, 9, 1, tzinfo=timezone.utc)


async def _seed(db_session, *, contracted=None, fraction=0.10):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Arm Intake Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(
        Client(
            id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file",
            control_arm_contracted_at=contracted,
            control_arm_fraction=fraction if contracted else None,
        )
    )
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id, client_id=client_id, name="Test Shop", address="123 Main St",
            lat=34.06, lng=-118.24, external_ref="SHOP-1",
        )
    )
    await db_session.commit()
    return hub_id, client_id


async def _ingest(db_session, hub_id, client_id, ref):
    return await ingest_order(
        db_session,
        HoldQueueStore(),
        hub_id=str(hub_id),
        client_id=str(client_id),
        source_system="flat_file",
        payload={
            "order_ref": ref,
            "shop_ref": "SHOP-1",
            "shop_lat": 34.06,
            "shop_lng": -118.24,
            "requested_at": NOW.isoformat(),
        },
    )


async def _assignment(db_session, order):
    return await db_session.scalar(
        select(ExperimentAssignment).where(ExperimentAssignment.order_id == order.id)
    )


def _control_position(client_id, stratum="__no_receiver__") -> int:
    """Which order to this dock the hash will put in control."""
    draw = _draw(f"{EXPERIMENT_CONTROL_ARM}:{client_id}", f"{stratum}:0")
    return min(int(draw * block_size(0.10)), block_size(0.10) - 1)


class TestOffUntilTheClauseIsRecorded:
    async def test_a_client_with_no_contract_date_gets_no_arm(
        self, db_session, real_redis_client
    ):
        """Every client today. The gate is a date somebody had to record, not a
        flag somebody could flip."""
        hub_id, client_id = await _seed(db_session, contracted=None)
        order = await _ingest(db_session, hub_id, client_id, "ORD-NOARM")
        assert await _assignment(db_session, order) is None

    async def test_the_order_is_unaffected(self, db_session, real_redis_client):
        hub_id, client_id = await _seed(db_session, contracted=None)
        order = await _ingest(db_session, hub_id, client_id, "ORD-NORMAL")
        assert order.hold_deadline > order.requested_at
        assert not [
            e for e in await outcomes_for(db_session, subject_id=order.id)
            if e.kind == KIND_ARM_ABSTENTION
        ]


class TestWhenTheArmIsLive:
    async def test_an_order_is_assigned_at_intake(self, db_session, real_redis_client):
        hub_id, client_id = await _seed(db_session, contracted=CONTRACTED)
        order = await _ingest(db_session, hub_id, client_id, "ORD-ARM-1")
        assignment = await _assignment(db_session, order)
        assert assignment is not None
        assert assignment.arm in (ARM_CONTROL, ARM_TREATMENT)

    async def test_an_order_with_no_delivery_address_uses_the_shared_stratum(
        self, db_session, real_redis_client
    ):
        """No adapter populates a delivery address yet, so this is the live path.
        Dropping those orders from the experiment would exclude them for a
        data-quality reason that has nothing to do with the customer."""
        hub_id, client_id = await _seed(db_session, contracted=CONTRACTED)
        order = await _ingest(db_session, hub_id, client_id, "ORD-ARM-2")
        assignment = await _assignment(db_session, order)
        assert assignment.receiver_key == "__no_receiver__"

    async def test_a_treatment_order_keeps_the_hold_it_was_classified_for(
        self, db_session, real_redis_client
    ):
        hub_id, client_id = await _seed(db_session, contracted=CONTRACTED)
        control_at = _control_position(client_id)
        # Any position but the chosen one is treatment.
        ref_index = (control_at + 1) % block_size(0.10)
        order = None
        for i in range(ref_index + 1):
            order = await _ingest(db_session, hub_id, client_id, f"ORD-T-{i}")
        assignment = await _assignment(db_session, order)
        assert assignment.arm == ARM_TREATMENT
        assert order.hold_deadline > order.requested_at
        assert not [
            e for e in await outcomes_for(db_session, subject_id=order.id)
            if e.kind == KIND_ARM_ABSTENTION
        ]


class TestTheAbstention:
    async def test_a_control_order_is_not_held_and_says_so(
        self, db_session, real_redis_client
    ):
        """The whole point of the arm: our batching hold does not apply. The
        deadline collapses to now so the queue releases it on the next cycle."""
        hub_id, client_id = await _seed(db_session, contracted=CONTRACTED)
        control_at = _control_position(client_id)
        order = None
        for i in range(control_at + 1):
            order = await _ingest(db_session, hub_id, client_id, f"ORD-C-{i}")

        assignment = await _assignment(db_session, order)
        assert assignment.arm == ARM_CONTROL
        entries = [
            e for e in await outcomes_for(db_session, subject_id=order.id)
            if e.kind == KIND_ARM_ABSTENTION
        ]
        assert len(entries) == 1
        assert entries[0].values["arm"] == ARM_CONTROL
        assert entries[0].values["declined"] == "hold"

    async def test_it_records_the_hold_we_declined_to_take(
        self, db_session, real_redis_client
    ):
        """An entry saying 'abstained' and nothing else proves the code ran, not
        that it did anything. The deadline is the size of what we gave up."""
        hub_id, client_id = await _seed(db_session, contracted=CONTRACTED)
        control_at = _control_position(client_id)
        order = None
        for i in range(control_at + 1):
            order = await _ingest(db_session, hub_id, client_id, f"ORD-W-{i}")

        entry = [
            e for e in await outcomes_for(db_session, subject_id=order.id)
            if e.kind == KIND_ARM_ABSTENTION
        ][0]
        would_have = datetime.fromisoformat(entry.values["would_have_held_until"])
        assert would_have > order.requested_at
        # And the hold we actually took is nothing.
        assert order.hold_deadline <= would_have


class TestItNeverCostsTheDelivery:
    async def test_an_order_with_no_dock_stays_out_when_any_dock_is_excluded(
        self, db_session, real_redis_client
    ):
        """Found by wiring this up. No adapter populates a delivery address yet,
        so an order arrives with no dock key - and if the customer has asked us
        to leave a dock out, we cannot show this order is not going to it. The
        promise was specific, so the order does not go in the arm. A smaller
        experiment at accounts that use exclusions is the right side to err on."""
        hub_id, client_id = await _seed(db_session, contracted=CONTRACTED)
        await exclude_receiver(
            db_session, client_id=client_id, receiver_key="1 fragile way, springfield",
            reason="customer asked", now=CONTRACTED,
        )
        await db_session.commit()
        order = await _ingest(db_session, hub_id, client_id, "ORD-EXCL")
        assert order.status.value == "held"
        assert await _assignment(db_session, order) is None

    async def test_with_no_exclusions_an_unkeyed_order_is_still_enrolled(
        self, db_session, real_redis_client
    ):
        hub_id, client_id = await _seed(db_session, contracted=CONTRACTED)
        order = await _ingest(db_session, hub_id, client_id, "ORD-OK")
        assert await _assignment(db_session, order) is not None

    async def test_an_arm_failure_does_not_fail_the_order(
        self, db_session, real_redis_client, monkeypatch
    ):
        """The measurement is worth less than the delivery. A customer whose
        orders bounced because of our control arm would be right to be angry."""
        import app.ingestion.service as service

        async def boom(*args, **kwargs):
            raise RuntimeError("experiment exploded")

        monkeypatch.setattr(service, "assign_arm", boom)
        hub_id, client_id = await _seed(db_session, contracted=CONTRACTED)
        order = await _ingest(db_session, hub_id, client_id, "ORD-BOOM")
        assert order.status.value == "held"
        assert await _assignment(db_session, order) is None
