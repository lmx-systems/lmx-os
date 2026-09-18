"""AGT-4: why is this order waiting, answered from the record.

*"Every explanation cites `REC-1`'s decision log rather than narrating. No
explanation the record cannot support."*

The second sentence is what most of this file is about. An explanation
assembled from current state would be a plausible story about the past, and a
dispatcher under pressure cannot tell a plausible story from a true one - so
the tests that matter here are the ones asserting the console *declines* to
answer, not the ones asserting it answers well.

Every plan is built as a real `CyclePlan` and written through
`record_decision`, never as hand-written JSON. That is deliberate: the first
version of the explainer read `assignment["stop_ids"]`, which is a derived
property on `RouteAssignment` and therefore absent from the stored blob
entirely. Against a fixture that invented the shape it passed; against a
snapshot the recorder actually wrote, every assigned order came back
unexplained.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.batch_queue import queue as queue_module
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.record import explain_order
from app.record.decisions import record_decision
from app.record.explain import _REASON_TEXT
from app.schemas.optimizer import (
    CyclePlan,
    DriverCandidate,
    HoldDecisionRecord,
    RouteAssignment,
    RouteVisit,
    StopCandidate,
)

pytestmark = pytest.mark.integration

ARRIVED = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)
DECIDED_AT = ARRIVED + timedelta(minutes=10)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Explain Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _order(db_session, hub, *, arrived=ARRIVED) -> Order:
    order = Order(
        hub_id=hub.id,
        external_order_ref=f"PO-{uuid.uuid4().hex[:8]}",
        source_system="flat_file",
        raw_payload={},
        sla_tier="T2",
        status=OrderStatus.held,
        requested_at=arrived,
    )
    db_session.add(order)
    await db_session.flush()
    return order


def _plan(
    hub_id: str,
    *,
    decided_at=DECIDED_AT,
    hold_decisions=(),
    assignments=(),
    unassigned=(),
    order_ids=(),
) -> CyclePlan:
    return CyclePlan(
        hub_id=hub_id,
        planned_at=decided_at,
        hub_closed=False,
        held_order_count=len(order_ids),
        released_order_ids=list(order_ids),
        shop_name_by_order_id={oid: "Shop" for oid in order_ids},
        fleet_snapshot=[],
        stops=[
            StopCandidate(
                stop_id=oid,
                order_ids=[oid],
                lat=30.26,
                lng=-97.74,
                weight_units=1.0,
                sla_tier="T2",
                collect_by=decided_at + timedelta(minutes=90),
            )
            for oid in order_ids
        ],
        drivers=[
            DriverCandidate(
                driver_id="driver-1", lat=30.27, lng=-97.74, capacity_remaining_units=40.0
            )
        ],
        assignments=list(assignments),
        unassigned_stop_ids=list(unassigned),
        hold_decisions=list(hold_decisions),
        engine="stub_nearest_neighbor",
        plan_duration_seconds=0.02,
    )


class TestItCitesTheRecord:
    async def test_a_hold_reason_is_reported_with_the_snapshot_that_holds_it(
        self, db_session
    ):
        """The plain case, and the one that did not exist before AGT-4: the
        queue had always produced this reason and `run_cycle` had always thrown
        it away."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        key = str(order.id)

        snapshot = await record_decision(
            db_session,
            _plan(
                str(hub.id),
                order_ids=[key],
                hold_decisions=[
                    HoldDecisionRecord(
                        order_id=key,
                        action="hold",
                        reason="no_cluster_mate_and_drivers_available",
                    )
                ],
            ),
        )

        explanation = await explain_order(db_session, order_id=order.id)

        assert explanation.is_explained
        (fact,) = explanation.facts
        assert "waiting for another order going the same way" in fact.statement
        assert fact.snapshot_id == snapshot.id
        assert fact.engine == "stub_nearest_neighbor"

    async def test_every_fact_carries_a_snapshot_id(self, db_session):
        """The done-when in one assertion. A line with no citation is narration
        however true it happens to be, because nobody reading it can check."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        key = str(order.id)

        await record_decision(
            db_session,
            _plan(
                str(hub.id),
                order_ids=[key],
                hold_decisions=[
                    HoldDecisionRecord(
                        order_id=key, action="hold", reason="no_available_drivers"
                    )
                ],
            ),
        )
        await record_decision(
            db_session,
            _plan(
                str(hub.id),
                decided_at=DECIDED_AT + timedelta(minutes=10),
                order_ids=[key],
                hold_decisions=[
                    HoldDecisionRecord(
                        order_id=key, action="release", reason="sla_hold_deadline_reached"
                    )
                ],
            ),
        )

        explanation = await explain_order(db_session, order_id=order.id)

        assert len(explanation.facts) == 2
        assert all(fact.snapshot_id is not None for fact in explanation.facts)

    async def test_the_facts_are_oldest_first(self, db_session):
        """A held order's story is a sequence - held, held, released - and read
        backwards it says the opposite of what happened."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        key = str(order.id)

        for minutes, reason in (
            (30, "sla_hold_deadline_reached"),
            (10, "no_available_drivers"),
        ):
            await record_decision(
                db_session,
                _plan(
                    str(hub.id),
                    decided_at=ARRIVED + timedelta(minutes=minutes),
                    order_ids=[key],
                    hold_decisions=[
                        HoldDecisionRecord(order_id=key, action="hold", reason=reason)
                    ],
                ),
            )

        explanation = await explain_order(db_session, order_id=order.id)

        assert [fact.at for fact in explanation.facts] == sorted(
            fact.at for fact in explanation.facts
        )
        assert "no driver was on shift" in explanation.facts[0].statement

    async def test_an_assignment_is_read_from_the_blob_the_recorder_writes(
        self, db_session
    ):
        """The regression test for the bug that produced this file's docstring.

        `RouteAssignment.stop_ids` is a property, so `model_dump()` omits it.
        Reading it found nothing, and an assigned order therefore reported as
        having nothing recorded about it - a false refusal, which is the same
        failure as a false explanation with better manners.
        """
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        key = str(order.id)

        await record_decision(
            db_session,
            _plan(
                str(hub.id),
                order_ids=[key],
                assignments=[
                    RouteAssignment(
                        driver_id="driver-1",
                        visits=[
                            RouteVisit(order_id=key, kind="pickup"),
                            RouteVisit(order_id=key, kind="delivery"),
                        ],
                    )
                ],
            ),
        )

        explanation = await explain_order(db_session, order_id=order.id)

        assert explanation.is_explained, "an assigned order must not read as unexplained"
        assert "driver-1" in explanation.facts[0].statement

    async def test_another_orders_decisions_are_not_borrowed(self, db_session):
        """Two orders in one cycle. An explainer that reported the whole
        snapshot would be confidently wrong about both."""
        hub = await _hub(db_session)
        mine = await _order(db_session, hub)
        theirs = await _order(db_session, hub)

        await record_decision(
            db_session,
            _plan(
                str(hub.id),
                order_ids=[str(mine.id), str(theirs.id)],
                hold_decisions=[
                    HoldDecisionRecord(
                        order_id=str(theirs.id),
                        action="release",
                        reason="cluster_mate_found",
                    )
                ],
            ),
        )

        explanation = await explain_order(db_session, order_id=mine.id)

        assert not explanation.is_explained
        assert "none recorded a decision about it" in explanation.unexplained


class TestItRefusesRatherThanInfers:
    async def test_an_order_no_cycle_decided_on_is_unexplained(self, db_session):
        """Not "it is waiting for a cluster mate" - which is what the order
        *looks* like. The queue may not have run, the hub may have closed, a
        driver may have gone off shift. Saying so would be narration."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        explanation = await explain_order(db_session, order_id=order.id)

        assert not explanation.is_explained
        assert explanation.facts == []
        assert "No dispatch cycle has run" in explanation.unexplained

    async def test_a_snapshot_predating_the_column_says_so(self, db_session):
        """Migration 0059 does not backfill, so old snapshots carry no reasons.
        "The reasons were never captured" and "this order had no reason" are
        different facts and only the first tells somebody what to fix."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        await record_decision(db_session, _plan(str(hub.id), order_ids=[str(order.id)]))

        explanation = await explain_order(db_session, order_id=order.id)

        assert not explanation.is_explained
        assert "never captured" in explanation.unexplained

    async def test_an_order_that_does_not_exist_is_refused_not_invented(self, db_session):
        explanation = await explain_order(db_session, order_id=uuid.uuid4())

        assert not explanation.is_explained
        assert "No such order" in explanation.unexplained

    async def test_a_cycle_that_ran_before_the_order_arrived_is_not_consulted(
        self, db_session
    ):
        """It cannot have decided anything about an order that did not exist,
        and an id collision in an old blob would otherwise produce a fact dated
        before the order."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        await record_decision(
            db_session,
            _plan(
                str(hub.id),
                decided_at=ARRIVED - timedelta(hours=1),
                order_ids=[str(order.id)],
                hold_decisions=[
                    HoldDecisionRecord(
                        order_id=str(order.id), action="hold", reason="no_available_drivers"
                    )
                ],
            ),
        )

        explanation = await explain_order(db_session, order_id=order.id)

        assert not explanation.is_explained

    async def test_another_hubs_cycle_is_not_consulted(self, db_session):
        hub = await _hub(db_session)
        elsewhere = await _hub(db_session)
        order = await _order(db_session, hub)

        await record_decision(
            db_session,
            _plan(
                str(elsewhere.id),
                order_ids=[str(order.id)],
                hold_decisions=[
                    HoldDecisionRecord(
                        order_id=str(order.id), action="hold", reason="no_available_drivers"
                    )
                ],
            ),
        )

        explanation = await explain_order(db_session, order_id=order.id)

        assert not explanation.is_explained


class TestTheReasonsStayInStep:
    async def test_every_reason_the_queue_emits_has_a_sentence(self):
        """The drift guard. `_REASON_TEXT` is keyed by the exact strings
        `app/batch_queue/queue.py` produces, so a new reason added there without
        a sentence here would reach a dispatcher as a bare identifier - and a
        retired one would leave a sentence the system can no longer say."""
        import ast
        import inspect

        emitted = {
            node.value.value
            for node in ast.walk(ast.parse(inspect.getsource(queue_module)))
            if isinstance(node, ast.keyword)
            and node.arg == "reason"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }

        assert emitted, "found no reasons in queue.py - the parse is wrong, not the code"
        assert emitted - set(_REASON_TEXT) == set(), "a queue reason has no sentence"
        assert set(_REASON_TEXT) - emitted == set(), "a sentence for a reason nobody emits"

    async def test_an_unmapped_reason_is_printed_raw_not_smoothed(self, db_session):
        """If the guard above is ever skipped, the failure must be a dispatcher
        seeing an unfamiliar identifier - not a plausible sentence this module
        made up to cover the gap."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        await record_decision(
            db_session,
            _plan(
                str(hub.id),
                order_ids=[str(order.id)],
                hold_decisions=[
                    HoldDecisionRecord(
                        order_id=str(order.id), action="hold", reason="some_future_reason"
                    )
                ],
            ),
        )

        explanation = await explain_order(db_session, order_id=order.id)

        assert "some_future_reason" in explanation.facts[0].statement


class TestTheEndpoint:
    async def test_it_returns_the_facts_and_the_refusal_flag(self, db_session):
        from app.api.routes import order_explanation
        from app.ops_auth.dependencies import AuthedOpsUser
        from app.models.ops_user import VIEWER_ROLE

        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        await record_decision(
            db_session,
            _plan(
                str(hub.id),
                order_ids=[str(order.id)],
                hold_decisions=[
                    HoldDecisionRecord(
                        order_id=str(order.id), action="hold", reason="no_available_drivers"
                    )
                ],
            ),
        )

        viewer = AuthedOpsUser(
            ops_user_id="u1", email="v@example.com", name="Viewer", role=VIEWER_ROLE
        )
        view = await order_explanation(order_id=order.id, session=db_session, _ops=viewer)

        assert view.is_explained is True
        assert view.unexplained is None
        assert view.facts[0].snapshot_id is not None

    async def test_an_unexplained_order_is_a_200_with_a_reason_not_a_404(
        self, db_session
    ):
        """"We have no record of deciding this" is an answer, and a dispatcher
        needs to see it. A 404 would read as "no such order", which is a
        different problem with a different fix."""
        from app.api.routes import order_explanation
        from app.ops_auth.dependencies import AuthedOpsUser
        from app.models.ops_user import VIEWER_ROLE

        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        viewer = AuthedOpsUser(
            ops_user_id="u1", email="v@example.com", name="Viewer", role=VIEWER_ROLE
        )
        view = await order_explanation(order_id=order.id, session=db_session, _ops=viewer)

        assert view.is_explained is False
        assert view.facts == []
        assert view.unexplained

    async def test_it_needs_an_ops_session(self):
        """Any ops user, like the exception queue it sits beside - but not
        nobody. It names drivers and customers."""
        import inspect

        from app.api.routes import order_explanation
        from app.ops_auth.dependencies import get_current_ops_user

        dependency = inspect.signature(order_explanation).parameters["_ops"].default
        assert dependency.dependency is get_current_ops_user
