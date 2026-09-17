"""EXP-2: no dock absorbs more than its share, and fragile docks can opt out.

The guarantee is arithmetic, so it is asserted as arithmetic rather than as a
tolerance. `TestNoDockAbsorbsMoreThanItsShare` also runs the design that was
rejected, on the same orders, and shows it failing - a docstring saying
"independent draws cluster" is an argument, and this is the measurement.
"""
import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.experiment.arms import (
    ARM_CONTROL,
    STRATUM_UNKNOWN_RECEIVER,
    _draw,
    assign_arm,
    block_size,
    verify_assignment,
)
from app.experiment.exclusions import (
    ReceiverExcluded,
    exclude_receiver,
    exclusion_impact,
    is_excluded,
    revoke_exclusion,
)
from app.models.client import Client
from app.models.experiment_exclusion import ExperimentExclusion
from app.models.hub import Hub
from app.models.order import Order, OrderStatus

pytestmark = pytest.mark.integration

CONTRACTED = datetime(2026, 9, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="EXP2 Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _client(db_session, hub, *, fraction=0.10) -> Client:
    client = Client(
        hub_id=hub.id, name=f"Client {uuid.uuid4().hex[:6]}", pos_system="flat_file",
        control_arm_fraction=fraction, control_arm_contracted_at=CONTRACTED,
    )
    db_session.add(client)
    await db_session.flush()
    return client


async def _order(db_session, hub, client) -> Order:
    order = Order(
        hub_id=hub.id, client_id=client.id,
        external_order_ref=f"PO-{uuid.uuid4().hex[:8]}", source_system="flat_file",
        raw_payload={}, sla_tier="T2", status=OrderStatus.held, requested_at=NOW,
    )
    db_session.add(order)
    await db_session.flush()
    return order


class TestNoDockAbsorbsMoreThanItsShare:
    async def test_a_full_block_holds_exactly_one_control_order(self, db_session):
        """The guarantee, stated as arithmetic. At a 10% arm the block is ten
        orders and exactly one of them is control - not on average, every time."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub, fraction=0.10)
        size = block_size(0.10)

        arms = []
        for _ in range(size * 3):
            order = await _order(db_session, hub, client)
            arms.append(
                (await assign_arm(db_session, order, client, receiver_key="dock-a",
                                  now=NOW)).arm
            )

        for block in range(3):
            window = arms[block * size : (block + 1) * size]
            assert window.count(ARM_CONTROL) == 1, f"block {block}: {window}"

    async def test_the_rejected_design_does_cluster_on_the_same_orders(self, db_session):
        """Why this was worth changing. Independent per-order draws - EXP-1 as
        first written - put the right fraction across a book and guarantee
        nothing about one dock. Over enough docks at least one lands in control
        more than its share, and four deliberately slower deliveries to one
        customer is a phone call rather than a statistic."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub, fraction=0.10)
        salt = f"exp-1-control-arm:{client.id}"

        worst_independent = 0
        for dock in range(40):
            controls = sum(
                1
                for n in range(20)
                if _draw(salt, f"{dock}-order-{n}") < 0.10
            )
            worst_independent = max(worst_independent, controls)

        allowed = math.ceil(20 / block_size(0.10))
        assert worst_independent > allowed, (
            "independent draws failed to over-concentrate on any dock in this "
            "sample, which would make the premise of the change wrong rather "
            "than the code"
        )

    async def test_no_dock_exceeds_its_share_across_a_realistic_book(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub, fraction=0.10)
        size = block_size(0.10)
        per_dock = 22
        counts: dict[str, int] = {}

        for dock in range(8):
            key = f"dock-{dock}"
            for _ in range(per_dock):
                order = await _order(db_session, hub, client)
                assignment = await assign_arm(
                    db_session, order, client, receiver_key=key, now=NOW
                )
                if assignment.arm == ARM_CONTROL:
                    counts[key] = counts.get(key, 0) + 1

        ceiling = math.ceil(per_dock / size)
        for key, control in counts.items():
            assert control <= ceiling, f"{key} took {control}, ceiling is {ceiling}"

    async def test_a_small_dock_is_not_quietly_excluded(self, db_session):
        """A dock with fewer orders than a block never completes one. Each of
        its orders still has a 1/block chance of being the chosen position, so
        its marginal rate is the contracted fraction - the stratification must
        not turn small docks into a treatment-only population."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub, fraction=0.10)
        size = block_size(0.10)

        controls = 0
        for dock in range(60):
            for _ in range(3):
                order = await _order(db_session, hub, client)
                assignment = await assign_arm(
                    db_session, order, client, receiver_key=f"small-{dock}", now=NOW
                )
                if assignment.arm == ARM_CONTROL:
                    controls += 1

        expected = 180 / size
        assert controls > 0, "no small dock ever reached the control arm"
        assert 0.4 * expected <= controls <= 2.0 * expected

    async def test_the_control_slot_moves_between_blocks(self, db_session):
        """One draw per block, not a fixed position. If the same slot were
        always chosen, every dock's control orders would land on the same day of
        its ordering cycle - systematic, and invisible in an aggregate rate."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub, fraction=0.10)
        size = block_size(0.10)

        positions = []
        for _ in range(size * 6):
            order = await _order(db_session, hub, client)
            assignment = await assign_arm(
                db_session, order, client, receiver_key="dock-z", now=NOW
            )
            if assignment.arm == ARM_CONTROL:
                positions.append(assignment.position_in_block)
        assert len(set(positions)) > 1

    async def test_positions_are_contiguous_with_no_gaps_or_repeats(self, db_session):
        """The count that decides a position runs under an advisory lock. A
        duplicate position would mean two intakes read the same count, and two
        control orders could land in a block that guarantees one."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub, fraction=0.10)
        size = block_size(0.10)

        seen = []
        for _ in range(size * 2):
            order = await _order(db_session, hub, client)
            a = await assign_arm(db_session, order, client, receiver_key="dock-q", now=NOW)
            seen.append((a.block_index, a.position_in_block))
        assert seen == [(i // size, i % size) for i in range(size * 2)]


class TestOrdersWithNoDock:
    async def test_they_fall_into_one_shared_stratum(self, db_session):
        """An address that names no place cannot be stratified by dock. Excluding
        those orders from the experiment would drop them for a data-quality
        reason that has nothing to do with the customer, so they stratify
        against the client instead - the fraction stays right, the per-dock
        guarantee does not apply, and the row says which."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order = await _order(db_session, hub, client)
        assignment = await assign_arm(db_session, order, client, now=NOW)
        assert assignment.receiver_key == STRATUM_UNKNOWN_RECEIVER


class TestExclusion:
    async def test_an_excluded_dock_gets_no_arm(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await exclude_receiver(
            db_session, client_id=client.id, receiver_key="fragile",
            reason="Largest account; owner asked to be left out", now=NOW,
        )
        order = await _order(db_session, hub, client)
        with pytest.raises(ReceiverExcluded, match="fragile"):
            await assign_arm(db_session, order, client, receiver_key="fragile", now=NOW)

    async def test_other_docks_are_unaffected(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await exclude_receiver(
            db_session, client_id=client.id, receiver_key="fragile",
            reason="asked", now=NOW,
        )
        order = await _order(db_session, hub, client)
        assert await assign_arm(db_session, order, client, receiver_key="ordinary", now=NOW)

    async def test_an_exclusion_needs_a_reason(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        with pytest.raises(ValueError, match="reason"):
            await exclude_receiver(
                db_session, client_id=client.id, receiver_key="x", reason="   ", now=NOW
            )

    async def test_the_database_also_refuses_an_empty_reason(self, db_session):
        """Enforced twice on purpose. The Python check catches the caller; the
        constraint catches a script that went round it."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await db_session.commit()
        with pytest.raises(Exception, match="ck_experiment_exclusions_reason"):
            await db_session.execute(
                text(
                    "INSERT INTO experiment_exclusions (id, client_id, receiver_key, "
                    "experiment, reason, requested_by, excluded_at) VALUES "
                    "(:id, :c, 'k', 'exp-1-control-arm', '  ', 'customer', :n)"
                ),
                {"id": uuid.uuid4(), "c": client.id, "n": NOW},
            )
        await db_session.rollback()

    async def test_excluding_twice_does_not_create_two_live_rows(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        first = await exclude_receiver(
            db_session, client_id=client.id, receiver_key="k", reason="asked", now=NOW
        )
        second = await exclude_receiver(
            db_session, client_id=client.id, receiver_key="k", reason="asked again",
            now=NOW,
        )
        assert first.id == second.id

    async def test_the_database_refuses_a_second_live_exclusion(self, db_session):
        """Two would be two answers to 'is this dock in the arm', and revoking
        one would leave the other standing - the customer told their dock was
        back in the measurement when it was not."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await exclude_receiver(
            db_session, client_id=client.id, receiver_key="k", reason="asked", now=NOW
        )
        await db_session.commit()
        db_session.add(
            ExperimentExclusion(
                client_id=client.id, receiver_key="k", experiment="exp-1-control-arm",
                reason="again", requested_by="customer", excluded_at=NOW,
            )
        )
        with pytest.raises(Exception):
            await db_session.flush()
        await db_session.rollback()

    async def test_revoking_keeps_the_row(self, db_session):
        """'This dock was excluded from March to June at the customer's request'
        is part of what a later statement has to explain, and a deleted row
        explains nothing."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await exclude_receiver(
            db_session, client_id=client.id, receiver_key="k", reason="asked", now=NOW
        )
        revoked = await revoke_exclusion(
            db_session, client_id=client.id, receiver_key="k",
            now=NOW + timedelta(days=30),
        )
        assert revoked is not None
        assert revoked.revoked_at is not None
        assert revoked.is_live is False
        assert await is_excluded(db_session, client_id=client.id, receiver_key="k") is False

    async def test_a_revoked_dock_can_be_assigned_again(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await exclude_receiver(
            db_session, client_id=client.id, receiver_key="k", reason="asked", now=NOW
        )
        await revoke_exclusion(db_session, client_id=client.id, receiver_key="k", now=NOW)
        order = await _order(db_session, hub, client)
        assert await assign_arm(db_session, order, client, receiver_key="k", now=NOW)

    async def test_requested_by_is_constrained(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        with pytest.raises(ValueError, match="requested_by"):
            await exclude_receiver(
                db_session, client_id=client.id, receiver_key="k", reason="r",
                requested_by="somebody", now=NOW,
            )


class TestTheExclusionIsDisclosed:
    async def test_it_reports_the_share_of_volume_left_out(self, db_session):
        """Excluding is a reasonable trade for a customer to make and an
        unreasonable one to make silently: a control arm measured on the calm
        traffic does not generalise to the docks that were removed."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await exclude_receiver(
            db_session, client_id=client.id, receiver_key="big",
            reason="largest account", now=NOW,
        )
        impact = await exclusion_impact(
            db_session, client_id=client.id,
            order_counts_by_receiver={"big": 300, "small": 700},
        )
        assert impact.live_exclusions == 1
        assert impact.orders_excluded == 300
        assert impact.share_of_orders == pytest.approx(0.3)
        assert "30.0%" in impact.disclosure()
        assert "does not cover" in impact.disclosure()

    async def test_it_says_so_plainly_when_nothing_is_excluded(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        impact = await exclusion_impact(db_session, client_id=client.id)
        assert impact.live_exclusions == 0
        assert impact.disclosure() == "No docks were excluded from the measurement."

    async def test_a_revoked_exclusion_still_counts_in_the_history(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await exclude_receiver(
            db_session, client_id=client.id, receiver_key="k", reason="asked", now=NOW
        )
        await revoke_exclusion(db_session, client_id=client.id, receiver_key="k", now=NOW)
        impact = await exclusion_impact(db_session, client_id=client.id)
        assert impact.live_exclusions == 0
        assert impact.revoked_exclusions == 1


class TestItIsStillVerifiable:
    async def test_a_block_assignment_recomputes(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order = await _order(db_session, hub, client)
        assignment = await assign_arm(db_session, order, client, receiver_key="d", now=NOW)
        assert verify_assignment(assignment) is True

    async def test_moving_an_order_out_of_the_control_slot_fails_verification(
        self, db_session
    ):
        """The trigger refuses the write; this catches a row altered some way the
        trigger never saw. Under stratification the position is the thing worth
        editing, so it is the thing worth checking."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub, fraction=0.10)
        size = block_size(0.10)
        control = None
        for _ in range(size * 2):
            order = await _order(db_session, hub, client)
            a = await assign_arm(db_session, order, client, receiver_key="d", now=NOW)
            if a.arm == ARM_CONTROL:
                control = a
                break
        assert control is not None
        control.position_in_block = (control.position_in_block + 1) % size
        assert verify_assignment(control) is False
