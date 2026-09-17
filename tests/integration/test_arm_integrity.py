"""EXP-3: is the arm still an arm, and may a statement be built from it?

Done when "a skewed or contaminated arm alerts before a statement is generated".
The gate matters more than the alert: the failure this catches does not produce
an obviously broken number, it produces a confident one.

Every fixture here is built the way `assign_arm` would build it, then broken on
purpose one property at a time. A monitor tested only against fabricated data
would be tested against data no real path can produce.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.experiment.arms import _draw, block_size
from app.experiment.integrity import (
    MAX_COVERAGE_GAP,
    MIN_ASSIGNMENTS_FOR_SKEW,
    SEVERITY_NOTES,
    SEVERITY_WARNS,
    check_arm_integrity,
    render,
    wilson_interval,
)
from app.models.client import Client
from app.models.experiment_assignment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    EXPERIMENT_CONTROL_ARM,
    ExperimentAssignment,
)
from app.models.hub import Hub
from app.models.outcome_entry import KIND_COST, SUBJECT_ORDER
from app.record.outcomes import record_outcome

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
MID = datetime(2026, 8, 15, tzinfo=timezone.utc)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Integrity Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _client(db_session, hub, fraction=0.10) -> Client:
    client = Client(
        hub_id=hub.id, name=f"C{uuid.uuid4().hex[:6]}", pos_system="flat_file",
        control_arm_fraction=fraction, control_arm_contracted_at=START,
    )
    db_session.add(client)
    await db_session.flush()
    return client


async def _clean_book(db_session, hub, client, *, docks=15, cost_control=True):
    """What `assign_arm` produces: one block per dock, one control slot in each."""
    salt = f"{EXPERIMENT_CONTROL_ARM}:{client.id}"
    size = block_size(client.control_arm_fraction)
    made = []
    for dock in range(docks):
        stratum = f"dock-{dock}"
        draw = _draw(salt, f"{stratum}:0")
        chosen = min(int(draw * size), size - 1)
        for position in range(size):
            order_id = uuid.uuid4()
            arm = ARM_CONTROL if position == chosen else ARM_TREATMENT
            db_session.add(
                ExperimentAssignment(
                    hub_id=hub.id, client_id=client.id, order_id=order_id,
                    experiment=EXPERIMENT_CONTROL_ARM, arm=arm, assigned_at=MID,
                    salt=salt, control_fraction=client.control_arm_fraction,
                    draw=draw, contracted_at=START, receiver_key=stratum,
                    block_size=size, block_index=0, position_in_block=position,
                )
            )
            made.append((order_id, arm))
    await db_session.flush()
    for order_id, arm in made:
        if arm == ARM_CONTROL and not cost_control:
            continue
        await record_outcome(
            db_session, hub_id=hub.id, subject_type=SUBJECT_ORDER,
            subject_id=order_id, kind=KIND_COST, occurred_at=MID,
            values={"loaded_cents": 900, "rate_source": "driver"},
        )
    return made


async def _inject(db_session, hub, client, *, arm, count=1, receiver_key="dock-0",
                  fraction=None, contracted=START, position=0, draw=None,
                  salt=None, block=0):
    """Insert rows that should not be there.

    **Inserted, never updated.** `experiment_assignments` refuses UPDATE by
    trigger - EXP-1's whole point - so a bad row cannot arrive by being edited.
    It arrives by being written: a restore from a dump, a backfill, a migration
    that recreated the table, a script that went round the ORM. That is the
    threat EXP-3 is actually guarding against, so it is the one the tests pose.
    """
    size = block_size(client.control_arm_fraction)
    row_salt = salt or f"{EXPERIMENT_CONTROL_ARM}:{client.id}"
    for index in range(count):
        # Unless a test is deliberately breaking verification, the injected row
        # has to recompute - otherwise every test would trip the verification
        # check first and never reach the one it is about.
        #
        # Verification recomputes the draw from the REAL hash of (salt, dock,
        # block), so the draw cannot be chosen: only the position can. Control
        # sits on the slot the hash picked; treatment sits anywhere else.
        if draw is None:
            row_draw = _draw(row_salt, f"{receiver_key}:{block + index}")
            chosen = min(int(row_draw * size), size - 1)
            slot = chosen if arm == ARM_CONTROL else (chosen + 1) % size
            row_block = block + index
        else:
            row_draw = draw
            slot = (position + index) % size
            row_block = block
        db_session.add(
            ExperimentAssignment(
                hub_id=hub.id, client_id=client.id, order_id=uuid.uuid4(),
                experiment=EXPERIMENT_CONTROL_ARM, arm=arm, assigned_at=MID,
                salt=row_salt,
                control_fraction=fraction or client.control_arm_fraction,
                draw=row_draw, contracted_at=contracted, receiver_key=receiver_key,
                block_size=size, block_index=row_block,
                position_in_block=slot,
            )
        )
    await db_session.flush()


async def _check(db_session, client):
    return await check_arm_integrity(
        db_session, client_id=client.id, since=START, until=END
    )


class TestACleanArmPasses:
    async def test_nothing_blocks(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client)
        report = await _check(db_session, client)
        assert report.blocks_a_statement is False, report.why_blocked()
        assert "Nothing here blocks a statement" in render(report)

    async def test_an_empty_window_notes_rather_than_blocks(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        report = await _check(db_session, client)
        assert report.blocks_a_statement is False
        assert any(f.check == "no-assignments" for f in report.findings)


class TestWhatItBlocks:
    async def test_a_row_that_no_longer_recomputes(self, db_session):
        """An assignment edited by something the append-only trigger never saw -
        a restore, a migration, manual surgery."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client)
        # A row whose stored draw does not produce its stored arm.
        await _inject(
            db_session, hub, client, arm=ARM_CONTROL,
            receiver_key="dock-99", draw=0.999999, position=0,
        )

        report = await _check(db_session, client)
        assert "assignments-verify" in {f.check for f in report.blocking}

    async def test_a_skewed_split(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client, docks=15)
        # A backfill that wrote control rows across a spread of docks - the
        # per-dock guarantee survives and the overall share does not.
        for dock in range(60):
            await _inject(
                db_session, hub, client, arm=ARM_CONTROL,
                receiver_key=f"backfill-{dock}", position=0,
            )

        report = await _check(db_session, client)
        assert "control-share" in {f.check for f in report.blocking}

    async def test_a_dock_over_its_share(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client, docks=15)
        # Four extra control rows at one dock, which is more than a ten-order
        # block can contain however many orders that dock has.
        await _inject(
            db_session, hub, client, arm=ARM_CONTROL, count=4,
            receiver_key="dock-0", position=0, block=1,
        )

        report = await _check(db_session, client)
        assert "per-dock-share" in {f.check for f in report.blocking}

    async def test_two_control_fractions_in_one_window(self, db_session):
        """Pooling them is two experiments described as one."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client)
        await _inject(
            db_session, hub, client, arm=ARM_TREATMENT, count=5,
            receiver_key="dock-2", fraction=0.05, block=2,
        )

        report = await _check(db_session, client)
        assert "terms-drift" in {f.check for f in report.blocking}

    async def test_control_orders_costed_less_often_than_the_rest(self, db_session):
        """The subtle one. The assignment stays perfectly fair and the
        comparison runs on whichever orders happened to get measured."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client, docks=25, cost_control=False)

        report = await _check(db_session, client)
        finding = next(
            (f for f in report.blocking if f.check == "differential-coverage"), None
        )
        assert finding is not None
        assert finding.numbers["control_coverage"] == 0.0
        assert finding.numbers["gap"] > MAX_COVERAGE_GAP


class TestWhatItOnlyWarnsAbout:
    async def test_a_position_handed_out_twice(self, db_session):
        """The advisory lock failing does not always breach the guarantee, and
        it is the earlier warning that it will."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client)
        # A second row at a position dock-1 already handed out.
        collided = await _dock_draw(client, "dock-1")
        await _inject(
            db_session, hub, client, arm=ARM_TREATMENT,
            receiver_key="dock-1", position=collided, block=0, draw=_real_draw(
                client, "dock-1", 0
            ),
        )

        report = await _check(db_session, client)
        severities = {f.check: f.severity for f in report.findings}
        assert severities.get("position-collisions") == SEVERITY_WARNS

    async def test_a_clause_that_changed_mid_period(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client)
        await _inject(
            db_session, hub, client, arm=ARM_TREATMENT, count=3,
            receiver_key="dock-3", contracted=START + timedelta(days=3), block=3,
        )

        report = await _check(db_session, client)
        severities = {f.check: f.severity for f in report.findings}
        assert severities.get("clause-drift") == SEVERITY_WARNS
        assert report.blocks_a_statement is False


class TestWhatItCannotCheck:
    async def test_it_says_so_on_every_run(self, db_session):
        """A monitor that listed six checks and stayed silent about the seventh
        would read as a clean bill. Nothing records that dispatch declined to
        act on a control order, so a contaminated one is indistinguishable."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client)
        report = await _check(db_session, client)
        note = next(
            f for f in report.findings
            if f.check == "dispatch-abstention-is-unverifiable"
        )
        assert note.severity == SEVERITY_NOTES
        assert "REC-1" in note.detail

    async def test_it_is_present_even_with_no_assignments(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        report = await _check(db_session, client)
        assert any(
            f.check == "dispatch-abstention-is-unverifiable" for f in report.findings
        )


class TestSkewIsNotJudgedTooEarly:
    async def test_a_thin_window_says_not_testable_rather_than_passing(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client, docks=3)
        report = await _check(db_session, client)
        finding = next(f for f in report.findings if f.check == "control-share")
        assert finding.severity == SEVERITY_NOTES
        assert "not a pass" in finding.detail
        assert report.assignments < MIN_ASSIGNMENTS_FOR_SKEW


class TestWilson:
    def test_it_is_not_wald(self):
        """At 5% on 100 trials, Wald's lower bound goes below zero and Wilson's
        does not. DATA_NEED_BRIEF.md §4.3 is explicit that the approximation
        fails on the side that matters, and a control arm lives in that regime."""
        low, high = wilson_interval(5, 100)
        assert low > 0.0
        assert low < 0.05 < high

    def test_it_widens_as_data_thins(self):
        wide = wilson_interval(5, 50)
        narrow = wilson_interval(50, 500)
        assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])

    def test_no_trials_admits_everything(self):
        assert wilson_interval(0, 0) == (0.0, 1.0)


class TestTheStatementIsGated:
    async def test_a_blocked_arm_produces_no_figure(self, db_session):
        """The done-when: alerts BEFORE a statement is generated. A monitor that
        ran afterwards would be a dashboard."""
        from app.settle.statement import build_statement, render_statement

        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client, docks=25, cost_control=False)

        statement = await build_statement(
            db_session, hub_id=hub.id, client_id=client.id,
            period_start=START, period_end=END,
        )
        assert statement.integrity is not None
        assert statement.integrity.blocks_a_statement
        assert statement.comparison is None
        text = render_statement(statement)
        assert "not putting a figure to it" in text
        # The customer gets the fact, not the check name.
        for internal in ("differential-coverage", "BLOCKS", "Wilson", "EXP-3"):
            assert internal not in text

    async def test_a_clean_arm_is_not_gated(self, db_session):
        from app.settle.statement import build_statement

        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _clean_book(db_session, hub, client, docks=35)
        statement = await build_statement(
            db_session, hub_id=hub.id, client_id=client.id,
            period_start=START, period_end=END,
        )
        assert statement.integrity.blocks_a_statement is False
        assert statement.comparison is not None


def _real_draw(client, receiver_key: str, block: int) -> float:
    return _draw(f"{EXPERIMENT_CONTROL_ARM}:{client.id}", f"{receiver_key}:{block}")


async def _dock_draw(client, receiver_key: str) -> int:
    """The position this dock's block already handed out."""
    size = block_size(client.control_arm_fraction)
    return min(int(_real_draw(client, receiver_key, 0) * size), size - 1)
