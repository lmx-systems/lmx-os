"""STL-1: the savings statement, and the claims it will not make.

Done when *"a customer can read it without a call."* Several of these assert
that the statement says something **worse** than the arithmetic would allow -
that the range includes zero, that the sample is too thin, that a wage was
invented. Those are the tests that matter: a statement only survives a customer's
own analyst if it reached the uncomfortable conclusion first.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.experiment.exclusions import exclude_receiver
from app.models.client import Client
from app.models.experiment_assignment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    EXPERIMENT_CONTROL_ARM,
    ExperimentAssignment,
)
from app.models.hub import Hub
from app.models.outcome_entry import KIND_COST, SUBJECT_ORDER, OutcomeEntry
from app.record.cost import RATE_FROM_DRIVER, RATE_PLACEHOLDER
from app.record.outcomes import record_outcome
from app.settle.statement import (
    MINIMUM_ARM_DROPS,
    SavingsStatement,
    build_statement,
    compare_arms,
    render_statement,
)

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
MID = datetime(2026, 8, 15, tzinfo=timezone.utc)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Settle Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _client(db_session, hub) -> Client:
    client = Client(
        hub_id=hub.id, name=f"Client {uuid.uuid4().hex[:6]}", pos_system="flat_file",
        control_arm_fraction=0.10, control_arm_contracted_at=START,
    )
    db_session.add(client)
    await db_session.flush()
    return client


async def _drop(db_session, hub, client, *, arm, cents, rate_source=RATE_FROM_DRIVER):
    """One assigned order with a recorded cost, which is all a statement needs."""
    order_id = uuid.uuid4()
    db_session.add(
        ExperimentAssignment(
            hub_id=hub.id, client_id=client.id, order_id=order_id,
            experiment=EXPERIMENT_CONTROL_ARM, arm=arm, assigned_at=MID,
            salt="s", control_fraction=0.10, draw=0.5, contracted_at=START,
            receiver_key="dock", block_size=10, block_index=0, position_in_block=0,
        )
    )
    await record_outcome(
        db_session, hub_id=hub.id, subject_type=SUBJECT_ORDER, subject_id=order_id,
        kind=KIND_COST, occurred_at=MID,
        values={"loaded_cents": cents, "rate_source": rate_source},
    )
    return order_id


async def _statement(db_session, hub, client, **kwargs) -> SavingsStatement:
    return await build_statement(
        db_session, hub_id=hub.id, client_id=client.id,
        period_start=START, period_end=END, **kwargs,
    )


class TestTheIntervalIsTheHeadline:
    async def test_a_clear_saving_is_stated_as_a_range(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        for i in range(60):
            await _drop(db_session, hub, client, arm=ARM_CONTROL, cents=1000 + i)
            await _drop(db_session, hub, client, arm=ARM_TREATMENT, cents=700 + i)

        statement = await _statement(db_session, hub, client)
        assert statement.comparison is not None
        assert statement.comparison.shows_a_saving
        assert "less per delivery" in statement.headline
        text = render_statement(statement)
        assert "95% confident" in text

    async def test_an_interval_spanning_zero_refuses_the_claim(self, db_session):
        """The test that matters. A midpoint of $0.40 on a range of -$2 to +$3
        is not a saving, and printing the midpoint is how a statement becomes
        indefensible the first time somebody checks it."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        for i in range(60):
            await _drop(db_session, hub, client, arm=ARM_CONTROL, cents=900 + (i * 37) % 600)
            await _drop(db_session, hub, client, arm=ARM_TREATMENT, cents=880 + (i * 53) % 600)

        statement = await _statement(db_session, hub, client)
        assert statement.comparison is not None
        assert statement.comparison.spans_zero
        assert statement.comparison.shows_a_saving is False
        assert "does not yet show a saving" in statement.headline
        assert "includes zero" in render_statement(statement)

    async def test_the_sign_runs_the_way_a_saving_runs(self, db_session):
        """Positive means treatment cost less. Stated this way round so nobody
        has to explain the sign in the meeting the statement was meant to avoid."""
        comparison = compare_arms([1000.0] * 40, [800.0] * 40)
        assert comparison is not None
        assert comparison.difference_cents == pytest.approx(200.0)

    async def test_a_worse_result_is_reported_not_hidden(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        for i in range(60):
            await _drop(db_session, hub, client, arm=ARM_CONTROL, cents=700 + i)
            await _drop(db_session, hub, client, arm=ARM_TREATMENT, cents=1000 + i)

        statement = await _statement(db_session, hub, client)
        assert statement.comparison.difference_cents < 0
        assert statement.comparison.shows_a_saving is False


class TestWhenItRefusesToCompare:
    async def test_too_few_drops_produces_no_interval(self, db_session):
        """A difference computed on twenty deliveries is mostly noise. Saying so
        is a better statement than a wide number that reads as a small one."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        for i in range(MINIMUM_ARM_DROPS - 1):
            await _drop(db_session, hub, client, arm=ARM_CONTROL, cents=1000)
            await _drop(db_session, hub, client, arm=ARM_TREATMENT, cents=700)

        statement = await _statement(db_session, hub, client)
        assert statement.comparison is None
        assert "minimum" in statement.comparison_unavailable
        assert "cannot show a saving yet" in statement.headline

    async def test_an_account_with_no_arm_says_why_in_plain_words(self, db_session):
        """Every account, today: EXP-1 ships off until a contract clause is
        recorded. The statement has to explain that without jargon."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        statement = await _statement(db_session, hub, client)
        assert statement.comparison is None
        assert "has not been switched on" in statement.comparison_unavailable
        assert "EXP-1" not in render_statement(statement)

    async def test_thirty_each_is_the_floor(self):
        assert compare_arms([1000.0] * 29, [700.0] * 40) is None
        assert compare_arms([1000.0] * 40, [700.0] * 29) is None
        assert compare_arms([1000.0] * 30, [700.0] * 30) is not None


class TestWhatItWillNotProduce:
    async def test_there_is_no_billable_amount_anywhere_on_it(self, db_session):
        """§2.2(d): a savings-share ledger is per-order and adversarial; this is
        sampled. A field a billing run could pick up would settle by default a
        commercial question that was killed twice and reopened on 13 September."""
        fields = set(SavingsStatement.__dataclass_fields__)
        for forbidden in ("amount_due", "invoice", "share", "payable", "billable"):
            assert not any(forbidden in name for name in fields), forbidden

    async def test_it_says_it_cannot_be_broken_down_per_order(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        statement = await _statement(db_session, hub, client)
        text = render_statement(statement)
        assert "not the basis of an invoice" in text
        assert "what any one order saved" in text

    async def test_it_says_what_the_cost_leaves_out(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        statement = await _statement(db_session, hub, client)
        assert "does not include fuel" in render_statement(statement)


class TestItCarriesItsBasis:
    async def test_a_placeholder_wage_is_named_in_the_statement(self, db_session):
        """REC-2 lets a cost be computed from an invented wage so the system is
        usable today. The statement is where that stops being acceptable
        silently."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        for _ in range(3):
            await _drop(db_session, hub, client, arm=ARM_TREATMENT, cents=900,
                        rate_source=RATE_PLACEHOLDER)

        statement = await _statement(db_session, hub, client)
        assert any("placeholder wage" in c for c in statement.caveats)
        assert "made up" in render_statement(statement)

    async def test_exclusions_are_disclosed_in_the_statement_itself(self, db_session):
        """EXP-2 lets a customer keep a dock out of the comparison. The cost of
        doing so belongs in front of them, not in a log."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await exclude_receiver(
            db_session, client_id=client.id, receiver_key="big",
            reason="largest account", now=START,
        )
        statement = await _statement(
            db_session, hub, client,
            order_counts_by_receiver={"big": 200, "rest": 800},
        )
        text = render_statement(statement)
        assert "What this does not cover" in text
        assert "20.0%" in text

    async def test_no_costed_deliveries_says_so_rather_than_showing_zero(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        db_session.add(
            ExperimentAssignment(
                hub_id=hub.id, client_id=client.id, order_id=uuid.uuid4(),
                experiment=EXPERIMENT_CONTROL_ARM, arm=ARM_TREATMENT, assigned_at=MID,
                salt="s", control_fraction=0.10, draw=0.5, contracted_at=START,
            )
        )
        await db_session.flush()
        statement = await _statement(db_session, hub, client)
        assert statement.cost_per_drop_cents is None
        assert "could be costed" in render_statement(statement)

    async def test_a_superseded_cost_is_read_at_its_correction(self, db_session):
        """REC-3 keeps both so the correction can be seen, not so both can be
        counted."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        order_id = await _drop(db_session, hub, client, arm=ARM_TREATMENT, cents=5000)
        original = (
            await db_session.scalars(
                select(OutcomeEntry).where(OutcomeEntry.subject_id == order_id)
            )
        ).all()[0]
        await record_outcome(
            db_session, hub_id=hub.id, subject_type=SUBJECT_ORDER, subject_id=order_id,
            kind=KIND_COST, occurred_at=MID,
            values={"loaded_cents": 1000, "rate_source": RATE_FROM_DRIVER},
            supersedes=original.id,
        )
        statement = await _statement(db_session, hub, client)
        assert statement.cost_per_drop_cents == pytest.approx(1000)

    async def test_the_period_is_honoured(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _drop(db_session, hub, client, arm=ARM_TREATMENT, cents=900)
        statement = await build_statement(
            db_session, hub_id=hub.id, client_id=client.id,
            period_start=END, period_end=END + timedelta(days=30),
        )
        assert statement.drops == 0
        assert statement.costed_drops == 0


class TestItReadsWithoutACall:
    async def test_the_uncomfortable_parts_are_in_the_body(self, db_session):
        """Not a footnote. A reader who stops halfway should not have stopped
        before a caveat that changes the number above it."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        statement = await _statement(db_session, hub, client)
        text = render_statement(statement)
        assert text.index(statement.headline) < text.index("What we delivered")
        assert "How to read this" in text

    async def test_it_uses_no_internal_vocabulary(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        for i in range(40):
            await _drop(db_session, hub, client, arm=ARM_CONTROL, cents=1000 + i)
            await _drop(db_session, hub, client, arm=ARM_TREATMENT, cents=700 + i)
        text = render_statement(await _statement(db_session, hub, client))
        for jargon in (
            "EXP-1", "REC-2", "STL-1", "control arm", "treatment", "Welch",
            "counterfactual", "confounded", "stratum",
        ):
            assert jargon not in text, jargon

    async def test_it_never_names_the_customer_or_their_town(self, db_session):
        """CLAUDE.md's naming rule covers customer-facing artifacts explicitly."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        text = render_statement(await _statement(db_session, hub, client))
        assert client.name not in text
