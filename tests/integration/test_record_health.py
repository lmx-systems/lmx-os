"""Reading the record back (`REC-1`..`REC-4`).

The audit's last gap. Every writer in the record layer was wired in the three
changes before this one, and **a writer that silently stops looks exactly like a
quiet week.** Nothing read any of it back, so the only way to know whether the
ledger was filling was to query the database by hand.

The tests worth reading are the refusals: a rate with no denominator, a writer
that has never written, and a delivery nobody promised anything about. Each is a
case where the easy thing to report is a number that looks fine.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.client import Client
from app.models.hub import Hub
from app.models.linkage_flag import KIND_REPEAT_VISIT, LinkageFlag
from app.models.ops_user import VIEWER_ROLE
from app.models.order import Order, OrderStatus
from app.models.outcome_entry import KIND_DELIVERED, SUBJECT_ORDER
from app.ops_auth.dependencies import AuthedOpsUser
from app.record.consequences import CONSEQUENCE_ESCALATION, record_consequence
from app.record.outcomes import record_outcome
from app.reporting.record_health import build_record_health

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
OPS = AuthedOpsUser(
    ops_user_id="u1", email="v@example.com", name="Viewer", role=VIEWER_ROLE
)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Health Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    hub._client_id = client.id
    return hub


async def _delivered(
    db_session, hub, *, on_time=True, at=None, snapshot_id=None, promised=True
) -> Order:
    at = at or NOW - timedelta(days=1)
    order = Order(
        hub_id=hub.id, client_id=hub._client_id,
        external_order_ref=f"PO-{uuid.uuid4().hex[:6]}", source_system="flat_file",
        raw_payload={}, sla_tier="T2", status=OrderStatus.delivered,
        requested_at=at - timedelta(hours=2), delivered_at=at,
    )
    db_session.add(order)
    await db_session.flush()

    values = {
        "delivered_at": at.isoformat(),
        "commitment_source": "tier_term" if promised else "none",
        "sla_tier": "T2",
    }
    if promised:
        values["on_time"] = on_time
        values["lateness_seconds"] = -60.0 if on_time else 60.0
    else:
        # A client with no contract term. Recorded explicitly so a later
        # on-time rate can exclude these rather than count them as successes.
        values["on_time"] = None
        values["lateness_seconds"] = None

    await record_outcome(
        db_session,
        hub_id=hub.id,
        subject_type=SUBJECT_ORDER,
        subject_id=order.id,
        kind=KIND_DELIVERED,
        occurred_at=at,
        values=values,
        decision_snapshot_id=snapshot_id,
    )
    await db_session.flush()
    return order


class TestTheOnTimeRateComesFromTheLedger:
    async def test_it_counts_what_the_ledger_says(self, db_session):
        hub = await _hub(db_session)
        for on_time in (True, True, True, False):
            await _delivered(db_session, hub, on_time=on_time)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        assert health.on_time.numerator == 3
        assert health.on_time.denominator == 4
        assert health.on_time.percentage == 75.0

    async def test_a_delivery_nobody_promised_anything_about_is_excluded(self, db_session):
        """Not counted as a success. A client with no contract term was promised
        nothing, and counting those would flatter the rate by exactly the orders
        we never committed to."""
        hub = await _hub(db_session)
        await _delivered(db_session, hub, on_time=True)
        await _delivered(db_session, hub, promised=False)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        assert health.on_time.denominator == 1
        assert health.outcomes_recorded == 2, "still a recorded outcome, just not judgeable"

    async def test_no_judgeable_delivery_refuses_rather_than_reporting_zero(self, db_session):
        """0% and "we cannot say" look identical as a number and mean opposite
        things - one is a service failure and the other is a missing contract."""
        hub = await _hub(db_session)
        await _delivered(db_session, hub, promised=False)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        assert health.on_time.percentage is None
        assert health.on_time.not_measured
        assert health.on_time_interval is None

    async def test_a_thin_sample_says_so(self, db_session):
        """"100% on time" at n=3 and at n=400 are the same string and different
        facts."""
        hub = await _hub(db_session)
        for _ in range(3):
            await _delivered(db_session, hub, on_time=True)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        assert health.on_time.percentage == 100.0
        assert health.on_time.is_thin

    async def test_the_interval_is_wilson_and_is_not_a_point(self, db_session):
        """`DATA_NEED_BRIEF.md` §4.3: the normal approximation is
        anti-conservative in exactly the small-proportion regime. At 3/3, Wald
        gives [100%, 100%] - which would say we are certain after three
        deliveries."""
        hub = await _hub(db_session)
        for _ in range(3):
            await _delivered(db_session, hub, on_time=True)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        low, high = health.on_time_interval
        assert low < 100.0, "a Wald interval would sit at 100 and claim certainty"
        assert high == pytest.approx(100.0, abs=0.1)

    async def test_deliveries_outside_the_window_are_not_counted(self, db_session):
        hub = await _hub(db_session)
        await _delivered(db_session, hub, on_time=False, at=NOW - timedelta(days=90))

        health = await build_record_health(db_session, hub_id=hub.id, window_days=30, now=NOW)

        assert health.outcomes_recorded == 0
        assert health.on_time.not_measured


class TestItNoticesAWriterThatStopped:
    async def test_a_writer_with_nothing_reports_no_last_written(self, db_session):
        """The whole point. Zero rows with no timestamp is a writer that has
        never run; zero rows with a timestamp from March is one that stopped -
        and both are zero."""
        hub = await _hub(db_session)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        by_name = {w.name: w for w in health.writers}
        assert by_name["Delivery outcomes (REC-3)"].rows_in_window == 0
        assert by_name["Delivery outcomes (REC-3)"].last_written_at is None

    async def test_every_writer_in_the_record_layer_is_reported(self, db_session):
        """One missing writer is a blind spot exactly where this panel is meant
        to be looking."""
        hub = await _hub(db_session)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        names = " ".join(w.name for w in health.writers)
        for item in ("REC-1", "REC-2", "REC-3", "REC-4"):
            assert item in names

    async def test_a_writer_that_wrote_reports_when(self, db_session):
        hub = await _hub(db_session)
        await _delivered(db_session, hub)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        rec3 = next(w for w in health.writers if "REC-3" in w.name)
        assert rec3.rows_in_window == 1
        assert rec3.last_written_at is not None

    async def test_consequences_and_flags_are_counted_too(self, db_session):
        hub = await _hub(db_session)
        order = await _delivered(db_session, hub, on_time=False)
        await record_consequence(
            db_session, order, CONSEQUENCE_ESCALATION, occurred_at=NOW - timedelta(hours=1)
        )
        db_session.add(
            LinkageFlag(
                hub_id=hub.id, kind=KIND_REPEAT_VISIT, fingerprint=uuid.uuid4().hex,
                subjects={}, detail="Two visits today", detected_at=NOW - timedelta(hours=2),
            )
        )
        await db_session.flush()

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        by_name = {w.name: w.rows_in_window for w in health.writers}
        assert by_name["Consequences and silences (REC-2)"] == 1
        assert by_name["Linkage flags (REC-4)"] == 1
        assert health.open_flags == 1
        assert health.labels["observed_consequences"] == 1


class TestTheDecisionLink:
    async def test_it_reports_how_many_outcomes_cite_a_cycle(self, db_session):
        """REC-1 and REC-3 were joinable by nothing until `snapshot_that_assigned`.
        This is what says whether the join is actually being made."""
        from app.record.decisions import record_decision
        from app.schemas.optimizer import CyclePlan, DriverCandidate

        hub = await _hub(db_session)
        snapshot = await record_decision(
            db_session,
            CyclePlan(
                hub_id=str(hub.id), planned_at=NOW - timedelta(days=2), hub_closed=False,
                held_order_count=0, released_order_ids=[], shop_name_by_order_id={},
                fleet_snapshot=[], stops=[],
                drivers=[
                    DriverCandidate(
                        driver_id="d1", lat=30.27, lng=-97.74, capacity_remaining_units=40.0
                    )
                ],
                assignments=[], unassigned_stop_ids=[], engine="stub_nearest_neighbor",
                plan_duration_seconds=0.01,
            ),
        )
        await _delivered(db_session, hub, snapshot_id=snapshot.id)
        await _delivered(db_session, hub)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        assert health.outcomes_recorded == 2
        assert health.outcomes_linked_to_a_decision == 1
        assert health.decision_link_rate.percentage == 50.0

    async def test_with_no_outcomes_the_link_rate_refuses(self, db_session):
        hub = await _hub(db_session)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        assert health.decision_link_rate.percentage is None
        assert health.decision_link_rate.not_measured


class TestTheLabelBandIsReportedAsABand:
    async def test_both_ends_are_reported_and_neither_is_a_verdict(self, db_session):
        """The brief states 500-1,000 and declines to say where inside it the
        model becomes trainable. A single "ready" flag would invent that."""
        hub = await _hub(db_session)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        assert health.labels["required_range_for_m2"] == (500, 1000)
        assert health.labels["at_band_minimum"] is False
        assert health.labels["at_band_target"] is False
        assert "ready" not in health.labels

    async def test_it_is_not_626(self, db_session):
        """626 is §4.3's Wilson sizing for M5's modality false-positive rate and
        belongs to a different model. It sits inside 500-1,000, so nothing would
        look wrong - which is why this is a test rather than a comment."""
        hub = await _hub(db_session)

        health = await build_record_health(db_session, hub_id=hub.id, now=NOW)

        assert 626 not in health.labels["required_range_for_m2"]


class TestTheEndpoint:
    async def test_it_returns_the_writers_and_the_band(self, db_session):
        from app.api.routes import record_health

        hub = await _hub(db_session)
        await _delivered(db_session, hub)

        view = await record_health(
            hub_id=hub.id, window_days=30, session=db_session, _ops=OPS
        )

        assert view.outcomes_recorded == 1
        assert len(view.writers) == 4
        assert tuple(view.labels["required_range_for_m2"]) == (500, 1000)

    async def test_it_is_open_to_any_ops_session(self):
        """A dispatcher who cannot see whether their own hub's record is being
        written cannot raise it when it stops."""
        import inspect

        from app.api.routes import record_health
        from app.ops_auth.dependencies import get_current_ops_user

        dependency = inspect.signature(record_health).parameters["_ops"].default
        assert dependency.dependency is get_current_ops_user

    async def test_it_never_recomputes_from_orders(self):
        """The one failure it exists to catch. A reader that fell back to
        recomputing from `orders` would keep showing a healthy on-time rate
        after the ledger stopped being written.

        Asserted on the imports rather than the source text: an earlier version
        searched for `Order.delivered_at` and failed on the module's own
        docstring, which names it to explain why it is not used. Prose about a
        thing is not a use of it, and a test that cannot tell the difference
        will be deleted the first time it cries wolf.
        """
        import ast
        import inspect

        from app.reporting import record_health as module

        imported = {
            alias.name
            for node in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "Order" not in imported, "the on-time rate must come from the ledger"
        assert "OutcomeEntry" in imported
