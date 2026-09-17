"""The switches the Phase 2 audit found missing, and the chain they complete.

Everything in Phase 2 was built, tested and off, and four of the things needed
to turn it on did not exist: nothing set a contract date, nothing recorded an
exclusion, nothing computed a cost, nothing produced a statement. A module with
tests and no entry point looks finished from every angle except that one.

`TestTheWholeChain` is the test that would have caught it. It runs the path a
real customer takes - enrol, ingest, cost, settle - and asserts a figure comes
out the far end.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.batch_queue.store import HoldQueueStore
from app.experiment.arms import (
    AlreadyEnrolled,
    control_arm_is_live,
    enrol_control_arm,
    withdraw_control_arm,
)
from app.ingestion.service import ingest_order
from app.models.client import Client
from app.models.driver import Driver
from app.models.driver_shift_event import DriverShiftEvent
from app.models.experiment_assignment import ARM_CONTROL, ExperimentAssignment
from app.models.hub import Hub
from app.models.outcome_entry import KIND_ARM_ABSTENTION, KIND_COST, OutcomeEntry
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.models.stop_geofence_event import KIND_ENTER, KIND_EXIT, StopGeofenceEvent
from app.record.cost import record_costs_for_period
from app.settle.statement import build_statement

pytestmark = pytest.mark.integration

DAY = datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 9, 17, tzinfo=timezone.utc)
UNTIL = SINCE + timedelta(days=1)
CONTRACTED = datetime(2026, 9, 1, tzinfo=timezone.utc)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Switch Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.commit()
    return hub


async def _client(db_session, hub, *, contracted=None, fraction=None) -> Client:
    client = Client(
        id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file",
        control_arm_contracted_at=contracted, control_arm_fraction=fraction,
    )
    db_session.add(client)
    await db_session.commit()
    return client


class TestTheEnrolmentSwitch:
    async def test_it_turns_the_arm_on(self, db_session):
        """Nothing in app/ or scripts/ set these columns before, so the only way
        to enrol a customer was SQL against production."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        assert control_arm_is_live(client) is False

        await enrol_control_arm(
            db_session, client, fraction=0.08, contracted_at=CONTRACTED
        )
        assert control_arm_is_live(client) is True
        assert client.control_arm_contracted_at == CONTRACTED

    async def test_a_fraction_outside_the_band_is_refused_with_the_reason(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        with pytest.raises(ValueError, match="outside the band"):
            await enrol_control_arm(
                db_session, client, fraction=0.25, contracted_at=CONTRACTED
            )

    async def test_changing_a_live_arm_needs_saying_so(self, db_session):
        """Two fractions pooled is two experiments described as one, which EXP-3
        blocks a statement on. Somebody should choose that rather than discover
        it."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub, contracted=CONTRACTED, fraction=0.08)
        with pytest.raises(AlreadyEnrolled, match="replacing=True"):
            await enrol_control_arm(
                db_session, client, fraction=0.10, contracted_at=CONTRACTED
            )
        await enrol_control_arm(
            db_session, client, fraction=0.10, contracted_at=CONTRACTED, replacing=True
        )
        assert client.control_arm_fraction == 0.10

    async def test_withdrawing_leaves_the_assignments_alone(self, db_session):
        """They are append-only evidence of what happened, and a customer
        leaving does not unmake the orders that were in it."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub, contracted=CONTRACTED, fraction=0.10)
        db_session.add(
            ExperimentAssignment(
                hub_id=hub.id, client_id=client.id, order_id=uuid.uuid4(),
                experiment="exp-1-control-arm", arm=ARM_CONTROL, assigned_at=DAY,
                salt="s", control_fraction=0.10, draw=0.05, contracted_at=CONTRACTED,
            )
        )
        await db_session.flush()

        await withdraw_control_arm(db_session, client)
        assert control_arm_is_live(client) is False
        remaining = list(
            await db_session.scalars(
                select(ExperimentAssignment).where(
                    ExperimentAssignment.client_id == client.id
                )
            )
        )
        assert len(remaining) == 1


class TestTheCostingSwitch:
    async def _driver_day(self, db_session, hub, *, rate=3000):
        driver = Driver(
            hub_id=hub.id, name="Driver", phone=f"+1512555{uuid.uuid4().hex[:4]}",
            hourly_rate_cents=rate,
        )
        db_session.add(driver)
        await db_session.flush()
        for kind, when in (("available", DAY), ("off_shift", DAY + timedelta(hours=4))):
            db_session.add(
                DriverShiftEvent(
                    driver_id=driver.id, hub_id=hub.id, event_type=kind, occurred_at=when
                )
            )
        route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
        db_session.add(route)
        await db_session.flush()

        from app.models.order import Order, OrderStatus

        orders = []
        for index in range(2):
            order = Order(
                hub_id=hub.id, external_order_ref=f"PO-{uuid.uuid4().hex[:8]}",
                source_system="flat_file", raw_payload={}, sla_tier="T2",
                status=OrderStatus.delivered, requested_at=DAY,
            )
            db_session.add(order)
            await db_session.flush()
            stop = Stop(
                route_id=route.id, sequence=index + 1, stop_type="dropoff", parcel_count=1
            )
            db_session.add(stop)
            await db_session.flush()
            db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
            arrive = DAY + timedelta(minutes=30 * index)
            for kind, when in ((KIND_ENTER, arrive), (KIND_EXIT, arrive + timedelta(minutes=8))):
                db_session.add(
                    StopGeofenceEvent(
                        stop_id=stop.id, kind=kind, occurred_at=when,
                        recorded_at=when, accuracy_m=10.0,
                    )
                )
            orders.append(order)
        await db_session.flush()
        return driver, orders

    async def _costs(self, db_session, orders) -> list:
        return list(
            await db_session.scalars(
                select(OutcomeEntry).where(
                    OutcomeEntry.subject_id.in_([o.id for o in orders]),
                    OutcomeEntry.kind == KIND_COST,
                )
            )
        )

    async def test_it_writes_a_cost_per_drop(self, db_session):
        """`record_driver_day_cost` had no caller anywhere, so nothing in this
        system had ever computed what a drop cost."""
        hub = await _hub(db_session)
        _driver, orders = await self._driver_day(db_session, hub)
        summary = await record_costs_for_period(
            db_session, hub_id=hub.id, since=SINCE, until=UNTIL
        )
        assert summary["costed"] == 2
        assert len(await self._costs(db_session, orders)) == 2

    async def test_running_it_twice_does_not_double_write(self, db_session):
        """An append-only ledger with two live costs for one order leaves
        nothing downstream able to choose between them."""
        hub = await _hub(db_session)
        _driver, orders = await self._driver_day(db_session, hub)
        await record_costs_for_period(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        second = await record_costs_for_period(
            db_session, hub_id=hub.id, since=SINCE, until=UNTIL
        )
        assert second["costed"] == 0
        assert second["skipped_already_costed"] == 2
        assert len(await self._costs(db_session, orders)) == 2

    async def test_recompute_supersedes_rather_than_duplicating(self, db_session):
        """The ledger's own answer to a corrected figure: both stay, the
        correction is visible, and the statement reads the one that supersedes."""
        hub = await _hub(db_session)
        _driver, orders = await self._driver_day(db_session, hub)
        await record_costs_for_period(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        again = await record_costs_for_period(
            db_session, hub_id=hub.id, since=SINCE, until=UNTIL, recompute=True
        )
        assert again["superseded"] == 2
        entries = await self._costs(db_session, orders)
        assert len(entries) == 4
        assert len([e for e in entries if e.supersedes is not None]) == 2

    async def test_it_names_the_days_costed_against_a_made_up_wage(self, db_session):
        hub = await _hub(db_session)
        await self._driver_day(db_session, hub, rate=None)
        summary = await record_costs_for_period(
            db_session, hub_id=hub.id, since=SINCE, until=UNTIL
        )
        assert summary["placeholder_rate_days"] == 1

    async def test_a_quiet_period_writes_nothing(self, db_session):
        hub = await _hub(db_session)
        await self._driver_day(db_session, hub)
        summary = await record_costs_for_period(
            db_session, hub_id=hub.id,
            since=SINCE + timedelta(days=10), until=UNTIL + timedelta(days=10),
        )
        assert summary == {
            "driver_days": 0, "costed": 0, "skipped_already_costed": 0,
            "superseded": 0, "placeholder_rate_days": 0,
        }


class TestTheWholeChain:
    async def test_enrol_ingest_cost_settle(self, db_session, real_redis_client):
        """The test that would have caught the audit's finding.

        Every step below existed and was tested before this ran; nothing joined
        them, so no order had ever been enrolled, costed or settled.
        """
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        db_session.add(
            Shop(
                id=uuid.uuid4(), client_id=client.id, name="Test Shop",
                address="123 Main St", lat=34.06, lng=-118.24, external_ref="SHOP-1",
            )
        )
        await db_session.commit()

        # 1. The switch that did not exist.
        await enrol_control_arm(
            db_session, client, fraction=0.10, contracted_at=CONTRACTED
        )
        await db_session.commit()

        # 2. Orders land and get an arm at intake.
        queue = HoldQueueStore()
        orders = []
        for index in range(12):
            orders.append(
                await ingest_order(
                    db_session, queue, hub_id=str(hub.id), client_id=str(client.id),
                    source_system="flat_file",
                    payload={
                        "order_ref": f"CHAIN-{index}", "shop_ref": "SHOP-1",
                        "shop_lat": 34.06, "shop_lng": -118.24,
                        "requested_at": DAY.isoformat(),
                    },
                )
            )
        assignments = list(
            await db_session.scalars(
                select(ExperimentAssignment).where(
                    ExperimentAssignment.client_id == client.id
                )
            )
        )
        assert len(assignments) == 12
        # Block size at a 10% arm is ten, so twelve orders fill one whole block
        # and start a second. The first block holds exactly one control order;
        # the two orders in the second are control only if the hash picked one
        # of their positions. Asserting a flat 1 would have passed for this
        # client id and failed for the next one.
        controls = [a for a in assignments if a.arm == ARM_CONTROL]
        first_block = [
            a for a in assignments
            if a.block_index == 0 and a.arm == ARM_CONTROL
        ]
        assert len(first_block) == 1, "a full block holds exactly one control order"
        assert 1 <= len(controls) <= 2

        # 3. The abstention intake writes for the control order.
        abstentions = list(
            await db_session.scalars(
                select(OutcomeEntry).where(OutcomeEntry.kind == KIND_ARM_ABSTENTION)
            )
        )
        assert len(abstentions) == len(controls)
        assert {a.subject_id for a in abstentions} == {c.order_id for c in controls}

        # 4. The statement runs end to end and refuses a figure, correctly:
        #    twelve orders is far below the thirty per arm it needs.
        statement = await build_statement(
            db_session, hub_id=hub.id, client_id=client.id,
            period_start=SINCE, period_end=UNTIL,
        )
        assert statement.drops == 12
        assert statement.comparison is None
        assert statement.integrity is not None
        assert statement.integrity.blocks_a_statement is False, (
            statement.integrity.why_blocked()
        )
        assert "Not enough deliveries yet" in statement.comparison_unavailable
