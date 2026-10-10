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

from app.experiment.arms import _draw, block_size
from app.experiment.exclusions import exclude_receiver
from app.models.client import Client
from app.models.driver import Driver
from app.models.driver_shift_event import DriverShiftEvent
from app.models.experiment_assignment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    EXPERIMENT_CONTROL_ARM,
    ExperimentAssignment,
)
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.outcome_entry import KIND_COST, SUBJECT_ORDER, OutcomeEntry
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.record.abstention import record_arm_abstention
from app.record.cost import RATE_FROM_DRIVER, RATE_PLACEHOLDER, record_costs_for_period
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


async def _book(
    db_session, hub, client, *, docks, control_cents, treatment_cents,
    rate_source=RATE_FROM_DRIVER,
):
    """A book of assignments that `EXP-3` will accept as genuine.

    The first version of this fabricated rows - an arbitrary salt, every order at
    one dock, a 50/50 split against a 10% arm - and EXP-3 blocked every statement
    built on it, correctly. Three of its checks fired at once and they were all
    right: the rows did not recompute, the share was nowhere near contracted, and
    one dock had taken every control order there was.

    So this builds what `assign_arm` would have: one block per dock, the control
    slot chosen by the same hash the real path uses, exactly one control order per
    block. Fast enough to keep in a test, and valid enough that a monitor
    designed to catch fabricated data does not catch it.
    """
    salt = f"{EXPERIMENT_CONTROL_ARM}:{client.id}"
    size = block_size(client.control_arm_fraction)
    control_ids, treatment_ids = [], []
    for dock in range(docks):
        stratum = f"dock-{dock}"
        draw = _draw(salt, f"{stratum}:0")
        chosen = min(int(draw * size), size - 1)
        for position in range(size):
            order_id = uuid.uuid4()
            is_control = position == chosen
            db_session.add(
                ExperimentAssignment(
                    hub_id=hub.id, client_id=client.id, order_id=order_id,
                    experiment=EXPERIMENT_CONTROL_ARM,
                    arm=ARM_CONTROL if is_control else ARM_TREATMENT,
                    assigned_at=MID, salt=salt,
                    control_fraction=client.control_arm_fraction, draw=draw,
                    contracted_at=START, receiver_key=stratum, block_size=size,
                    block_index=0, position_in_block=position,
                )
            )
            (control_ids if is_control else treatment_ids).append(order_id)
    await db_session.flush()
    for order_id in control_ids:
        # EXP-3 blocks a statement built on control orders with no record that we
        # declined to hold them. Intake writes these for real.
        await record_arm_abstention(
            db_session, hub_id=hub.id, order_id=order_id, arm=ARM_CONTROL,
            occurred_at=MID, would_have_held_until=MID,
        )

    for ids, cents_for in (
        (control_ids, control_cents), (treatment_ids, treatment_cents)
    ):
        for index, order_id in enumerate(ids):
            await record_outcome(
                db_session, hub_id=hub.id, subject_type=SUBJECT_ORDER,
                subject_id=order_id, kind=KIND_COST, occurred_at=MID,
                values={"loaded_cents": cents_for(index), "rate_source": rate_source},
            )
    return control_ids, treatment_ids


async def _delivered_on(db_session, hub, client, *, at: datetime, timed: bool = True) -> Order:
    """One assigned order, delivered at `at` by a driver on shift around it,
    with both taps recorded - everything costing needs, and nothing more.
    `timed=False` drops the arrival tap, the case completion leaves."""
    driver = Driver(
        hub_id=hub.id, name="Driver", phone=f"+1512555{uuid.uuid4().hex[:4]}",
        hourly_rate_cents=3_000,
    )
    order = Order(
        hub_id=hub.id, external_order_ref=f"PO-{uuid.uuid4().hex[:8]}",
        source_system="flat_file", raw_payload={}, sla_tier="T2",
        status=OrderStatus.delivered, requested_at=at - timedelta(hours=3),
    )
    db_session.add_all([driver, order])
    await db_session.flush()
    for kind, when in (("available", at - timedelta(hours=1)), ("off_shift", at + timedelta(hours=1))):
        db_session.add(
            DriverShiftEvent(driver_id=driver.id, hub_id=hub.id, event_type=kind, occurred_at=when)
        )
    db_session.add(
        ExperimentAssignment(
            hub_id=hub.id, client_id=client.id, order_id=order.id,
            experiment=EXPERIMENT_CONTROL_ARM, arm=ARM_TREATMENT,
            assigned_at=at - timedelta(hours=2), salt="s", control_fraction=0.10,
            draw=0.5, contracted_at=START,
        )
    )
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)
    await db_session.flush()
    stop = Stop(
        route_id=route.id, sequence=1, stop_type="dropoff", parcel_count=1,
        arrived_at=at if timed else None, completed_at=at + timedelta(minutes=10),
    )
    db_session.add(stop)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    await db_session.flush()
    return order


async def _statement(db_session, hub, client, **kwargs) -> SavingsStatement:
    return await build_statement(
        db_session, hub_id=hub.id, client_id=client.id,
        period_start=START, period_end=END, **kwargs,
    )


class TestTheIntervalIsTheHeadline:
    async def test_a_clear_saving_is_stated_as_a_range(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _book(
            db_session, hub, client, docks=35,
            control_cents=lambda i: 1000 + i, treatment_cents=lambda i: 700 + i,
        )

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
        await _book(
            db_session, hub, client, docks=35,
            control_cents=lambda i: 900 + (i * 37) % 600,
            treatment_cents=lambda i: 880 + (i * 53) % 600,
        )

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
        await _book(
            db_session, hub, client, docks=35,
            control_cents=lambda i: 700 + i, treatment_cents=lambda i: 1000 + i,
        )

        statement = await _statement(db_session, hub, client)
        assert statement.comparison.difference_cents < 0
        assert statement.comparison.shows_a_saving is False


class TestWhenItRefusesToCompare:
    async def test_too_few_drops_produces_no_interval(self, db_session):
        """A difference computed on twenty deliveries is mostly noise. Saying so
        is a better statement than a wide number that reads as a small one."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _book(
            db_session, hub, client, docks=MINIMUM_ARM_DROPS - 1,
            control_cents=lambda i: 1000, treatment_cents=lambda i: 700,
        )

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
        await _book(
            db_session, hub, client, docks=2,
            control_cents=lambda i: 900, treatment_cents=lambda i: 900,
            rate_source=RATE_PLACEHOLDER,
        )

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
        # No one at the hub was on shift, so this time the hours are the reason.
        assert "hours on shift" in statement.no_cost_reason

    async def test_with_hours_on_record_it_does_not_blame_the_hours(self, db_session):
        """The old sentence said shift hours were missing whatever the cause.
        Here they are recorded and the arrival is what is missing."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _delivered_on(db_session, hub, client, at=MID + timedelta(hours=9), timed=False)
        await record_costs_for_period(db_session, hub_id=hub.id, since=START, until=END)

        statement = await _statement(db_session, hub, client)

        assert statement.costed_drops == 0
        assert statement.no_cost_reason is not None
        assert "arrived" in statement.no_cost_reason
        assert "hours" not in statement.no_cost_reason
        assert statement.no_cost_reason in render_statement(statement)

    async def test_when_it_cannot_see_why_it_says_only_that_nothing_is_costed(self, db_session):
        """Hours and both taps on record, and costing never run for the period.
        Nothing the statement can see is missing, so it names no cause."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _delivered_on(db_session, hub, client, at=MID + timedelta(hours=9))

        statement = await _statement(db_session, hub, client)

        assert statement.no_cost_reason == (
            "None of them has been costed yet, so there is no average to show."
        )

    async def test_with_no_deliveries_it_explains_no_costing(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        statement = await _statement(db_session, hub, client)
        text = render_statement(statement)
        assert statement.drops == 0
        assert statement.no_cost_reason is None
        assert "costed" not in text.split("The comparison")[0]

    async def test_a_superseded_cost_is_read_at_its_correction(self, db_session):
        """REC-3 keeps both so the correction can be seen, not so both can be
        counted."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        _, treatment_ids = await _book(
            db_session, hub, client, docks=1,
            control_cents=lambda i: 5000, treatment_cents=lambda i: 5000,
        )
        order_id = treatment_ids[0]
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
        # Nine drops at 5000 and the corrected one at 1000, so the mean moves by
        # exactly the correction rather than counting the order twice.
        assert statement.cost_per_drop_cents == pytest.approx((9 * 5000 + 1000) / 10)

    async def test_the_period_is_honoured(self, db_session):
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _book(
            db_session, hub, client, docks=1,
            control_cents=lambda i: 900, treatment_cents=lambda i: 900,
        )
        statement = await build_statement(
            db_session, hub_id=hub.id, client_id=client.id,
            period_start=END, period_end=END + timedelta(days=30),
        )
        assert statement.drops == 0
        assert statement.costed_drops == 0

    @pytest.mark.parametrize(
        "delivered_at",
        [START + timedelta(hours=9), MID + timedelta(hours=9), END - timedelta(hours=15)],
        ids=["first-day", "mid-period", "last-day"],
    )
    async def test_every_day_of_the_period_is_costed(self, db_session, delivered_at):
        """Costing stamps a day's cost at the end of the day it covers, so the
        last day of a period is stamped exactly at `period_end` - the one
        instant a half-open window leaves out."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        await _delivered_on(db_session, hub, client, at=delivered_at)
        await record_costs_for_period(db_session, hub_id=hub.id, since=START, until=END)

        statement = await _statement(db_session, hub, client)

        assert statement.drops == 1
        assert statement.costed_drops == 1

    async def test_deliveries_with_no_arrival_are_said_beside_the_average(self, db_session):
        """Their driver time sits in other deliveries' costs, which changes what
        the average means - so the count goes next to the average, not under
        "How to read this" where a reader may never get to it."""
        hub = await _hub(db_session)
        client = await _client(db_session, hub)
        control_ids, _ = await _book(
            db_session, hub, client, docks=35,
            control_cents=lambda i: 1000 + i, treatment_cents=lambda i: 700 + i,
        )
        driver = Driver(hub_id=hub.id, name="Driver", phone=f"+1512555{uuid.uuid4().hex[:4]}")
        order = Order(
            id=control_ids[0], hub_id=hub.id, external_order_ref=f"PO-{uuid.uuid4().hex[:8]}",
            source_system="flat_file", raw_payload={}, sla_tier="T2",
            status=OrderStatus.delivered, requested_at=MID,
        )
        db_session.add_all([driver, order])
        await db_session.flush()
        route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
        db_session.add(route)
        await db_session.flush()
        stop = Stop(
            route_id=route.id, sequence=1, stop_type="dropoff", parcel_count=1,
            completed_at=MID,
        )
        db_session.add(stop)
        await db_session.flush()
        db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
        await db_session.flush()

        statement = await _statement(db_session, hub, client)
        text = render_statement(statement)

        assert statement.untimed_deliveries == 1
        disclosure = statement.untimed_disclosure
        assert disclosure is not None
        assert disclosure.startswith(
            f"One of the {statement.drops} deliveries has no recorded arrival time"
        )
        assert (
            text.index("What we delivered") < text.index(disclosure)
            < text.index("The comparison")
        )


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
        await _book(
            db_session, hub, client, docks=35,
            control_cents=lambda i: 1000 + i, treatment_cents=lambda i: 700 + i,
        )
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
