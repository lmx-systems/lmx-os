"""DEC-0's second half: the delta, and the metrics it refuses to produce.

The refusals get as many tests as the measurements. A harness that computes a
savings figure from shadow rows would produce a number nobody could check and
everybody would quote, and the only thing standing between here and that number
is a decision to not write it - so the decision is asserted.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.shadow_decision import ShadowDecision, ShadowOrderDecision
from app.models.stop import Stop, StopOrder
from app.reporting.measurement import Measurement, Rate
from app.shadow.divergence import (
    AGREED,
    DIFFERENT_DRIVER,
    DIFFERENT_SEQUENCE,
    NEITHER_PLACED,
    ONLY_THE_OPERATION_PLACED,
    REFUSED,
    SHADOW_WOULD_HAVE_PLACED,
    compute_divergence,
    render,
)

pytestmark = pytest.mark.integration

PLANNED = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 7, 6, tzinfo=timezone.utc)
UNTIL = datetime(2026, 7, 7, tzinfo=timezone.utc)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Divergence Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _driver(db_session, hub, name="Driver") -> Driver:
    driver = Driver(hub_id=hub.id, name=name, phone=f"+1512555{uuid.uuid4().hex[:4]}")
    db_session.add(driver)
    await db_session.flush()
    return driver


async def _order(db_session, hub, *, assigned_at=None) -> Order:
    order = Order(
        hub_id=hub.id,
        external_order_ref=f"PO-{uuid.uuid4().hex[:8]}",
        source_system="flat_file",
        raw_payload={},
        sla_tier="T2",
        status=OrderStatus.held,
        requested_at=PLANNED,
        assigned_at=assigned_at,
    )
    db_session.add(order)
    await db_session.flush()
    return order


async def _cycle(db_session, hub, *, engine="stub", planned_at=PLANNED, payload=None):
    cycle = ShadowDecision(
        hub_id=hub.id,
        planned_at=planned_at,
        hub_closed=False,
        engine=engine,
        plan_duration_seconds=0.4,
        held_order_count=0,
        released_order_count=0,
        driver_count=1,
        assigned_order_count=0,
        unassigned_order_count=0,
        assignment_payload=payload or [],
    )
    db_session.add(cycle)
    await db_session.flush()
    return cycle


async def _shadow_says(
    db_session, cycle, hub, order, *, decision="assigned", driver=None, index=0,
    planned_at=PLANNED,
):
    db_session.add(
        ShadowOrderDecision(
            shadow_decision_id=cycle.id,
            order_id=order.id,
            hub_id=hub.id,
            planned_at=planned_at,
            decision=decision,
            driver_id=driver.id if driver else None,
            sequence_index=index if decision == "assigned" else None,
            sla_tier="T2",
        )
    )
    await db_session.flush()


async def _really_went_to(db_session, hub, driver, orders: list[Order]):
    """Build the route the operation actually ran: pickup then dropoff, per
    order, numbered from one - the shape `app/optimizer/service.py` creates."""
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)
    await db_session.flush()
    sequence = 1
    for order in orders:
        for kind in ("pickup", "dropoff"):
            stop = Stop(route_id=route.id, sequence=sequence, stop_type=kind, parcel_count=1)
            db_session.add(stop)
            await db_session.flush()
            db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
            sequence += 1
    await db_session.flush()
    return route


class TestGrading:
    async def test_the_same_driver_in_the_same_place_is_agreement(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub, assigned_at=PLANNED + timedelta(minutes=10))
        cycle = await _cycle(db_session, hub)
        await _shadow_says(db_session, cycle, hub, order, driver=driver, index=0)
        await _really_went_to(db_session, hub, driver, [order])

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert [o.divergence for o in report.orders] == [AGREED]

    async def test_a_one_based_stop_number_is_not_a_sequence_disagreement(self, db_session):
        """The bug this test exists for: real stops number from 1 and there are
        two per order, shadow indexes orders from 0. Compared raw, the first
        order on every route grades as a sequence disagreement and the whole
        report is noise."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        first = await _order(db_session, hub, assigned_at=PLANNED)
        second = await _order(db_session, hub, assigned_at=PLANNED)
        cycle = await _cycle(db_session, hub)
        await _shadow_says(db_session, cycle, hub, first, driver=driver, index=0)
        await _shadow_says(db_session, cycle, hub, second, driver=driver, index=1)
        # Real route: stops 1,2 for the first order and 3,4 for the second.
        await _really_went_to(db_session, hub, driver, [first, second])

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert {o.divergence for o in report.orders} == {AGREED}

    async def test_the_same_driver_in_a_different_order_is_graded_apart(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        first = await _order(db_session, hub, assigned_at=PLANNED)
        second = await _order(db_session, hub, assigned_at=PLANNED)
        cycle = await _cycle(db_session, hub)
        # Shadow would have visited them the other way round.
        await _shadow_says(db_session, cycle, hub, first, driver=driver, index=1)
        await _shadow_says(db_session, cycle, hub, second, driver=driver, index=0)
        await _really_went_to(db_session, hub, driver, [first, second])

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert {o.divergence for o in report.orders} == {DIFFERENT_SEQUENCE}

    async def test_a_different_driver_is_the_real_disagreement(self, db_session):
        hub = await _hub(db_session)
        ours = await _driver(db_session, hub, name="Ours")
        theirs = await _driver(db_session, hub, name="Theirs")
        order = await _order(db_session, hub, assigned_at=PLANNED)
        cycle = await _cycle(db_session, hub)
        await _shadow_says(db_session, cycle, hub, order, driver=ours)
        await _really_went_to(db_session, hub, theirs, [order])

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert [o.divergence for o in report.orders] == [DIFFERENT_DRIVER]

    async def test_an_order_only_we_would_have_moved(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub)
        cycle = await _cycle(db_session, hub)
        await _shadow_says(db_session, cycle, hub, order, driver=driver)

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert [o.divergence for o in report.orders] == [SHADOW_WOULD_HAVE_PLACED]

    async def test_an_order_only_they_moved(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub, assigned_at=PLANNED)
        cycle = await _cycle(db_session, hub)
        await _shadow_says(db_session, cycle, hub, order, decision="unassigned")
        await _really_went_to(db_session, hub, driver, [order])

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert [o.divergence for o in report.orders] == [ONLY_THE_OPERATION_PLACED]

    async def test_neither_placed_it(self, db_session):
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        cycle = await _cycle(db_session, hub)
        await _shadow_says(db_session, cycle, hub, order, decision="unassigned")

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert [o.divergence for o in report.orders] == [NEITHER_PLACED]


class TestTheEarliestCommitmentWins:
    async def test_a_held_order_is_judged_from_the_cycle_that_would_have_moved_it(
        self, db_session
    ):
        """An order appears in every cycle until it is released. DEC-0 asks when
        LMX OS would have committed it, so the first assigning cycle is the
        answer; taking the last would flatter the lead time by discarding every
        cycle where we would have moved and the operation had not."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub, assigned_at=PLANNED + timedelta(hours=2))

        early = await _cycle(db_session, hub, planned_at=PLANNED)
        late = await _cycle(db_session, hub, planned_at=PLANNED + timedelta(hours=1))
        await _shadow_says(db_session, cycle=early, hub=hub, order=order, driver=driver,
                           planned_at=PLANNED)
        await _shadow_says(db_session, cycle=late, hub=hub, order=order, driver=driver,
                           planned_at=PLANNED + timedelta(hours=1))
        await _really_went_to(db_session, hub, driver, [order])

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert len(report.orders) == 1
        assert report.orders[0].dispatch_lead_seconds == pytest.approx(7200)

    async def test_a_cycle_that_could_not_place_it_is_not_a_commitment(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub, assigned_at=PLANNED + timedelta(hours=2))
        first = await _cycle(db_session, hub, planned_at=PLANNED)
        second = await _cycle(db_session, hub, planned_at=PLANNED + timedelta(hours=1))
        await _shadow_says(db_session, cycle=first, hub=hub, order=order,
                           decision="unassigned", planned_at=PLANNED)
        await _shadow_says(db_session, cycle=second, hub=hub, order=order, driver=driver,
                           planned_at=PLANNED + timedelta(hours=1))
        await _really_went_to(db_session, hub, driver, [order])

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert report.orders[0].divergence == AGREED
        assert report.orders[0].dispatch_lead_seconds == pytest.approx(3600)


class TestTheDispatchLead:
    async def test_it_is_negative_when_the_dispatcher_was_faster(self, db_session):
        """The likely finding, not a failure. `ROADMAP_1.5.md` Phase 3: 50.2% of
        orders already get in-flight insertion by hand and those reach customers
        faster. A report that could only express improvement would be useless."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub, assigned_at=PLANNED - timedelta(minutes=20))
        cycle = await _cycle(db_session, hub)
        await _shadow_says(db_session, cycle, hub, order, driver=driver)
        await _really_went_to(db_session, hub, driver, [order])

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert report.orders[0].dispatch_lead_seconds == pytest.approx(-1200)

    async def test_an_order_the_operation_never_committed_has_no_lead(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub)
        cycle = await _cycle(db_session, hub)
        await _shadow_says(db_session, cycle, hub, order, driver=driver)

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert report.orders[0].dispatch_lead_seconds is None


class TestWhatItWillNotCompute:
    async def test_there_is_no_cost_per_drop_and_the_reason_is_printed(self, db_session):
        """The shadow plan was never driven, so its cost is our own estimate.
        Comparing an estimate to a realised cost puts every optimism error on
        our side of the ledger."""
        hub = await _hub(db_session)
        await _cycle(db_session, hub)
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)

        names = {m.name for m in report.metrics}
        assert not any("cost" in n for n in names)
        assert "cost_per_drop_delta" in report.refused
        assert "EXP-1" in report.refused["cost_per_drop_delta"]

    async def test_there_is_no_on_time_or_savings_number(self, db_session):
        hub = await _hub(db_session)
        await _cycle(db_session, hub)
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert set(REFUSED) <= set(report.refused)
        for reason in report.refused.values():
            assert len(reason) > 40, "a refusal without a reason reads as an oversight"

    async def test_the_refusals_are_printed_beside_the_metrics(self, db_session):
        hub = await _hub(db_session)
        await _cycle(db_session, hub)
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        text = render(report)
        assert "not computed from shadow data, and why" in text
        assert "savings" in text


class TestTheReport:
    async def test_a_stub_solver_is_declared(self, db_session):
        """A stub plan and a Google plan are not comparable evidence. DEC-3 has
        never been run against a live project, so most rows will say stub and
        the report has to say so out loud."""
        hub = await _hub(db_session)
        # The name the stub actually reports. An equality check against "stub"
        # skipped the warning in exactly the case it exists for.
        await _cycle(db_session, hub, engine="stub_nearest_neighbor")
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert "not comparable evidence" in render(report)
        assert report.engines == {"stub_nearest_neighbor": 1}

    async def test_a_live_solver_gets_no_warning(self, db_session):
        hub = await _hub(db_session)
        await _cycle(db_session, hub, engine="google_routes")
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert "not comparable evidence" not in render(report)

    async def test_an_empty_window_reports_rather_than_divides_by_zero(self, db_session):
        hub = await _hub(db_session)
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert report.cycles == 0
        assert report.orders == []
        assert report.agreement_rate().not_measured
        assert render(report)

    async def test_a_thin_agreement_sample_says_so(self, db_session):
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        order = await _order(db_session, hub, assigned_at=PLANNED)
        cycle = await _cycle(db_session, hub)
        await _shadow_says(db_session, cycle, hub, order, driver=driver)
        await _really_went_to(db_session, hub, driver, [order])

        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert report.agreement_rate().is_thin

    async def test_orders_per_planned_route_reads_the_payload_the_recorder_writes(
        self, db_session
    ):
        """`stop_ids` is the recorder's name for what are actually order ids. A
        guess at `order_ids` would have silently produced no metric at all."""
        hub = await _hub(db_session)
        driver = await _driver(db_session, hub)
        await _cycle(
            db_session, hub,
            payload=[{"driver_id": str(driver.id),
                      "stop_ids": [str(uuid.uuid4()) for _ in range(4)]}],
        )
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        per_route = next(m for m in report.metrics if m.name == "orders per planned route")
        assert per_route.median == pytest.approx(4)

    async def test_every_metric_is_a_measurement_or_a_rate(self, db_session):
        """Both carry `not_measured`, which is why they were reused rather than
        returning bare floats: an absent number must be able to say why."""
        hub = await _hub(db_session)
        await _cycle(db_session, hub)
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert report.metrics
        for metric in report.metrics:
            assert isinstance(metric, (Measurement, Rate))

    async def test_it_ignores_cycles_outside_the_window(self, db_session):
        hub = await _hub(db_session)
        await _cycle(db_session, hub, planned_at=PLANNED - timedelta(days=3))
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert report.cycles == 0

    async def test_it_ignores_another_hub(self, db_session):
        hub = await _hub(db_session)
        other = await _hub(db_session)
        await _cycle(db_session, other)
        report = await compute_divergence(db_session, hub_id=hub.id, since=SINCE, until=UNTIL)
        assert report.cycles == 0
