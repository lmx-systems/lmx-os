"""EXP-1: the control arm, and the three things that make it one.

*"Arm assigned at intake, immutable, in the contract before the code."* Each
clause has its own class here, because each one fails differently and each
failure destroys the measurement in a way that is invisible afterwards.

The arm is what turns the central claim from modelled to observed. A
cost-per-drop delta computed from the order book projects our own assumptions;
one measured against 5-10% of orders dispatched as the customer would have is
evidence. That only holds if the split is genuinely random, genuinely fixed,
and genuinely disclosed - and none of those survives being approximately true.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.experiment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    ArmNotContractedError,
    arm_for_order,
    assign_arm,
    control_arm_is_live,
)
from app.experiment.arms import MAX_CONTROL_FRACTION, MIN_CONTROL_FRACTION, verify_assignment
from app.models.client import Client
from app.models.hub import Hub
from app.models.order import Order, OrderStatus

pytestmark = pytest.mark.integration

CONTRACTED = datetime(2026, 9, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Arm Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _client(db_session, hub, *, fraction=0.10, contracted=CONTRACTED) -> Client:
    client = Client(
        hub_id=hub.id, name=f"Client {uuid.uuid4().hex[:6]}", pos_system="flat_file",
        control_arm_fraction=fraction, control_arm_contracted_at=contracted,
    )
    db_session.add(client)
    await db_session.flush()
    return client


async def _order(db_session, hub, client) -> Order:
    order = Order(
        hub_id=hub.id, client_id=client.id, external_order_ref=f"PO-{uuid.uuid4().hex[:8]}",
        source_system="flat_file", raw_payload={}, sla_tier="T2",
        status=OrderStatus.held, requested_at=NOW,
    )
    db_session.add(order)
    await db_session.flush()
    return order


class TestInTheContractBeforeTheCode:
    """The clause that is a gate rather than a property."""

    async def test_a_client_with_no_clause_gets_no_arm(self, db_session):
        hub = await _hub(db_session)
        client = Client(hub_id=hub.id, name="Unenrolled", pos_system="flat_file")
        db_session.add(client)
        await db_session.flush()
        order = await _order(db_session, hub, client)

        with pytest.raises(ArmNotContractedError, match="contract before"):
            await assign_arm(db_session, order, client)

    async def test_that_is_the_default_for_every_client(self, db_session):
        """Off unless somebody enrols them. A migration that switched the arm
        on for the current book would be a disclosure failure performed by a
        deploy."""
        hub = await _hub(db_session)
        client = Client(hub_id=hub.id, name="Default", pos_system="flat_file")
        db_session.add(client)
        await db_session.flush()

        assert client.control_arm_contracted_at is None
        assert control_arm_is_live(client) is False

    async def test_a_fraction_without_a_date_is_refused_by_the_database(self, db_session):
        """Half-configuring the arm - a fraction set by a script, no clause -
        must not be possible, because it looks exactly like an enrolled client
        from the code's point of view."""
        hub = await _hub(db_session)
        await db_session.commit()

        with pytest.raises(Exception, match="ck_clients_control_arm_contracted"):
            await db_session.execute(
                text(
                    "INSERT INTO clients (id, hub_id, name, pos_system, control_arm_fraction) "
                    "VALUES (:id, :hub, 'Half configured', 'flat_file', 0.08)"
                ),
                {"id": uuid.uuid4(), "hub": hub.id},
            )
        await db_session.rollback()

    async def test_the_date_is_copied_onto_every_assignment(self, db_session):
        """"Was I in an experiment, and did I agree to it" must be answerable
        from the assignment, not from a client row that may since have
        changed."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order = await _order(db_session, hub, client)

        assignment = await assign_arm(db_session, order, client, now=NOW)
        assert assignment.contracted_at == CONTRACTED

    @pytest.mark.parametrize("fraction", [0.01, 0.04, 0.11, 0.5])
    async def test_a_fraction_outside_the_band_is_not_live(self, db_session, fraction):
        """Below 5% the control group says nothing within a quarter; above 10%
        we are giving a paying customer a worse service on more orders than the
        measurement needs. Both ends are a promise to somebody."""
        hub = await _hub(db_session)
        client = Client(
            hub_id=hub.id, name="Out of band", pos_system="flat_file",
            control_arm_fraction=fraction, control_arm_contracted_at=CONTRACTED,
        )
        assert control_arm_is_live(client) is False


class TestImmutable:
    async def test_assigning_twice_returns_the_same_arm(self, db_session):
        """Intake can be retried. A retry that re-rolled would bias the split
        towards whichever arm the retry happened to land in."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order = await _order(db_session, hub, client)

        first = await assign_arm(db_session, order, client, now=NOW)
        second = await assign_arm(db_session, order, client, now=NOW + timedelta(hours=1))

        assert first.id == second.id
        assert first.arm == second.arm

    async def test_the_database_refuses_to_move_an_order_between_arms(self, db_session):
        """The failure this guards against is not fraud. It is a well-meaning
        engineer moving one order out of control because the customer
        complained - which is exactly the order whose presence in the control
        group the measurement depends on."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order = await _order(db_session, hub, client)
        assignment = await assign_arm(db_session, order, client, now=NOW)
        await db_session.commit()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text("UPDATE experiment_assignments SET arm = 'treatment' WHERE id = :id"),
                {"id": assignment.id},
            )
        await db_session.rollback()

    async def test_the_database_refuses_to_delete_an_assignment(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order = await _order(db_session, hub, client)
        assignment = await assign_arm(db_session, order, client, now=NOW)
        await db_session.commit()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text("DELETE FROM experiment_assignments WHERE id = :id"),
                {"id": assignment.id},
            )
        await db_session.rollback()

    async def test_one_order_cannot_hold_two_arms(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order = await _order(db_session, hub, client)
        await assign_arm(db_session, order, client, now=NOW)
        await db_session.commit()

        with pytest.raises(Exception):
            await db_session.execute(
                text(
                    "INSERT INTO experiment_assignments (id, hub_id, order_id, experiment, "
                    "arm, assigned_at, salt, control_fraction, draw, contracted_at) VALUES "
                    "(:id, :hub, :order, 'exp-1-control-arm', 'control', :now, 's', 0.1, 0.01, :c)"
                ),
                {"id": uuid.uuid4(), "hub": hub.id, "order": order.id, "now": NOW, "c": CONTRACTED},
            )
        await db_session.rollback()


class TestTheSplitIsVerifiable:
    async def test_an_assignment_can_be_recomputed_rather_than_trusted(self, db_session):
        """A random draw leaves nothing behind - you can assert the split was
        fair but not show it. A hash can be recomputed by anyone holding the
        row, which is what EXP-3 will check and what makes a re-roll visible."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order = await _order(db_session, hub, client)

        assignment = await assign_arm(db_session, order, client, now=NOW)
        assert verify_assignment(assignment) is True

    async def test_a_tampered_arm_fails_verification(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order = await _order(db_session, hub, client)
        assignment = await assign_arm(db_session, order, client, now=NOW)

        # In memory only - the trigger would refuse the write, which is the
        # other defence. This is the one that catches a row altered some way
        # the trigger never saw.
        assignment.arm = ARM_CONTROL if assignment.arm == ARM_TREATMENT else ARM_TREATMENT
        assert verify_assignment(assignment) is False

    async def test_the_split_lands_near_the_configured_fraction(self, db_session):
        """Not a test of the hash - a test that the fraction means what it
        says. A 10% arm that delivered 30% would be giving three times as many
        customers a worse service as anyone agreed to."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub, fraction=0.10)

        arms = []
        for _ in range(400):
            order = await _order(db_session, hub, client)
            arms.append((await assign_arm(db_session, order, client, now=NOW)).arm)

        share = arms.count(ARM_CONTROL) / len(arms)
        assert 0.05 <= share <= 0.16, f"control share {share:.2%} is not near 10%"

    async def test_two_clients_are_assigned_independently(self, db_session):
        """A single global salt would correlate the arms of every order sharing
        a reference format - the kind of non-randomness that survives a casual
        look at the split."""
        hub = await _hub(db_session)
        one = await _client(db_session, hub)
        two = await _client(db_session, hub)
        order_one = await _order(db_session, hub, one)
        order_two = await _order(db_session, hub, two)

        a = await assign_arm(db_session, order_one, one, now=NOW)
        b = await assign_arm(db_session, order_two, two, now=NOW)
        assert a.salt != b.salt


class TestReading:
    async def test_an_unenrolled_order_reads_as_none_not_treatment(self, db_session):
        """A caller treating None as treatment would sweep every unenrolled
        customer into the comparison."""
        hub = await _hub(db_session)
        client = Client(hub_id=hub.id, name="Unenrolled", pos_system="flat_file")
        db_session.add(client)
        await db_session.flush()
        order = await _order(db_session, hub, client)

        assert await arm_for_order(db_session, order.id) is None

    async def test_the_band_is_what_the_roadmap_says(self):
        assert (MIN_CONTROL_FRACTION, MAX_CONTROL_FRACTION) == (0.05, 0.10)
