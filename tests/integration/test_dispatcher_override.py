"""CON-2 and CON-3: the override, the reason, and the label.

*"No override completes without a reason."* *"Every override lands in the
decision log as a labelled example."*

Both done-whens needed the override built first - nothing in `app/api/` let a
dispatcher release a held order or hold a released one, so the mandatory reason
code had nothing to attach to.

The tests are grouped by which failure they catch, because the reason
requirement is enforced in three places and each stops something different: the
schema stops a malformed request, the CHECK constraint stops a script, and the
note rule stops a reason that is present and says nothing. A single test
asserting "reason required" would pass with any one of the three in place.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.fleet_state.manager import FleetStateManager
from app.optimizer.event_trigger import dispatch_event_bus
from app.optimizer.google_routes_client import RouteOptimizationClient
from app.optimizer.service import DispatchOptimizerService
from app.schemas.fleet import DriverLocation, DriverState

from app.models.dispatcher_override import (
    REASON_CODES,
    REASON_CODES_REQUIRING_NOTE,
    REASON_LABELS,
    DispatcherOverride,
)
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.record.decisions import record_decision
from app.record.explain import explain_order
from app.record.overrides import (
    OverrideRefused,
    apply_override,
    labelled_overrides,
    overrides_for_order,
)
from app.schemas.optimizer import (
    CyclePlan,
    DriverCandidate,
    HoldDecisionRecord,
    StopCandidate,
)

pytestmark = pytest.mark.integration

ARRIVED = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)
DECIDED_AT = ARRIVED + timedelta(minutes=10)
OVERRODE_AT = ARRIVED + timedelta(minutes=20)

USER = {"ops_user_id": "u-1", "ops_user_email": "dispatcher@lmxit.com"}


@pytest.fixture(autouse=True)
def published(monkeypatch):
    """The cycles the endpoint asks for, caught here rather than sent to Redis."""
    events = []

    async def publish(hub_id, event_type):
        events.append((hub_id, event_type))

    monkeypatch.setattr(dispatch_event_bus, "publish", publish)
    return events


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Override Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


async def _order(db_session, hub, *, status=OrderStatus.held) -> Order:
    order = Order(
        hub_id=hub.id,
        external_order_ref=f"PO-{uuid.uuid4().hex[:8]}",
        source_system="flat_file",
        raw_payload={},
        sla_tier="T2",
        status=status,
        requested_at=ARRIVED,
    )
    db_session.add(order)
    await db_session.flush()
    return order


async def _system_held(
    db_session,
    hub,
    order,
    *,
    reason="waiting_for_cluster_mate",
    action="keep_holding",
    at=DECIDED_AT,
):
    """Record the queue deciding something about this order, the way a real
    cycle would - through `record_decision`, never by writing the JSON. The
    default is a hold the queue really makes, in its own words: it keeps a
    lone order while it waits for another going the same way."""
    key = str(order.id)
    return await record_decision(
        db_session,
        CyclePlan(
            hub_id=str(hub.id),
            planned_at=at,
            hub_closed=False,
            held_order_count=1,
            released_order_ids=[],
            shop_name_by_order_id={key: "Shop"},
            fleet_snapshot=[],
            stops=[
                StopCandidate(
                    stop_id=key,
                    order_ids=[key],
                    lat=30.26,
                    lng=-97.74,
                    weight_units=1.0,
                    sla_tier="T2",
                    collect_by=DECIDED_AT + timedelta(minutes=90),
                )
            ],
            drivers=[
                DriverCandidate(
                    driver_id="driver-1", lat=30.27, lng=-97.74, capacity_remaining_units=40.0
                )
            ],
            assignments=[],
            unassigned_stop_ids=[],
            hold_decisions=[
                HoldDecisionRecord(order_id=key, action=action, reason=reason)
            ],
            engine="stub_nearest_neighbor",
            plan_duration_seconds=0.02,
        ),
    )


class TestNoOverrideCompletesWithoutAReason:
    """CON-2, once per place it is enforced."""

    async def test_an_unknown_reason_code_is_refused(self, db_session):
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        with pytest.raises(OverrideRefused, match="not a reason code"):
            await apply_override(
                db_session, order_id=order.id, action="release",
                reason_code="because_i_said_so", **USER,
            )

    async def test_other_without_a_note_is_refused(self, db_session):
        """The failure a closed vocabulary cannot see. `other` with nothing
        written is an override with no reason wearing the costume of one, and it
        is what a hurried dispatcher reaches for every time if it is allowed."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        with pytest.raises(OverrideRefused, match="with nothing written"):
            await apply_override(
                db_session, order_id=order.id, action="release",
                reason_code="other", **USER,
            )

    async def test_a_note_of_only_whitespace_does_not_count(self, db_session):
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        with pytest.raises(OverrideRefused, match="with nothing written"):
            await apply_override(
                db_session, order_id=order.id, action="release",
                reason_code="other", note="   ", **USER,
            )

    async def test_the_database_refuses_a_row_with_no_reason(self, db_session):
        """The API refusing a request is the API's promise. CON-2 says no
        override *completes* without a reason, which has to hold for a script,
        a fixture, and an endpoint somebody adds later without reading this."""
        hub = await _hub(db_session)
        await db_session.commit()

        with pytest.raises(Exception, match="reason_code"):
            await db_session.execute(
                text(
                    "INSERT INTO dispatcher_overrides (id, hub_id, order_id, "
                    "overridden_at, ops_user_id, ops_user_email, action, "
                    "reason_code, system_decision_known, order_status_before) "
                    "VALUES (:id, :hub, :order, :now, 'u', 'e@x.com', 'release', "
                    "NULL, false, 'held')"
                ),
                {"id": uuid.uuid4(), "hub": hub.id, "order": uuid.uuid4(), "now": OVERRODE_AT},
            )
        await db_session.rollback()

    async def test_the_database_refuses_a_reason_outside_the_vocabulary(self, db_session):
        hub = await _hub(db_session)
        await db_session.commit()

        with pytest.raises(Exception, match="ck_dispatcher_overrides_reason_code"):
            await db_session.execute(
                text(
                    "INSERT INTO dispatcher_overrides (id, hub_id, order_id, "
                    "overridden_at, ops_user_id, ops_user_email, action, "
                    "reason_code, system_decision_known, order_status_before) "
                    "VALUES (:id, :hub, :order, :now, 'u', 'e@x.com', 'release', "
                    "'made_up', false, 'held')"
                ),
                {"id": uuid.uuid4(), "hub": hub.id, "order": uuid.uuid4(), "now": OVERRODE_AT},
            )
        await db_session.rollback()

    async def test_an_empty_note_is_refused_by_the_database(self, db_session):
        """An empty string satisfies NOT NULL while carrying nothing, and reads
        in an export as though somebody wrote an explanation."""
        hub = await _hub(db_session)
        await db_session.commit()

        with pytest.raises(Exception, match="note_not_blank"):
            await db_session.execute(
                text(
                    "INSERT INTO dispatcher_overrides (id, hub_id, order_id, "
                    "overridden_at, ops_user_id, ops_user_email, action, "
                    "reason_code, note, system_decision_known, order_status_before) "
                    "VALUES (:id, :hub, :order, :now, 'u', 'e@x.com', 'release', "
                    "'other', '  ', false, 'held')"
                ),
                {"id": uuid.uuid4(), "hub": hub.id, "order": uuid.uuid4(), "now": OVERRODE_AT},
            )
        await db_session.rollback()

    async def test_the_vocabulary_the_code_offers_is_the_one_the_database_accepts(
        self, db_session
    ):
        """A drift here is invisible until a dispatcher picks the one code the
        database rejects, at the moment they are least able to absorb it."""
        hub = await _hub(db_session)
        for code in REASON_CODES:
            order = await _order(db_session, hub)
            note = "because" if code in REASON_CODES_REQUIRING_NOTE else None
            await apply_override(
                db_session, order_id=order.id, action="release",
                reason_code=code, note=note, **USER,
            )
        await db_session.flush()

        assert set(REASON_LABELS) == set(REASON_CODES), "every code needs a label"


class TestEveryOverrideIsALabelledExample:
    """CON-3, and the cases where it honestly is not one."""

    async def test_the_systems_decision_is_copied_onto_the_override(self, db_session):
        """The pair is the training example: the queue said hold because nothing
        going the same way had arrived yet, the dispatcher said release because
        the customer called."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        snapshot = await _system_held(db_session, hub, order)

        outcome = await apply_override(
            db_session, order_id=order.id, action="release",
            reason_code="customer_called", **USER,
        )

        override = outcome.override
        assert override.system_decision_known is True
        assert override.system_action == "keep_holding"
        assert override.system_reason == "waiting_for_cluster_mate"
        assert override.cited_snapshot_id == snapshot.id
        assert override.is_labelled_example
        assert outcome.contradicted_the_system

    async def test_an_override_of_nothing_is_recorded_but_not_labelled(self, db_session):
        """No cycle decided anything about this order. The override is real and
        is recorded in full - it is just not evidence of the queue being wrong,
        because nothing says what the queue thought."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        outcome = await apply_override(
            db_session, order_id=order.id, action="release",
            reason_code="customer_called", **USER,
        )

        override = outcome.override
        assert override.system_decision_known is False
        assert override.system_action is None
        assert override.cited_snapshot_id is None
        assert not override.is_labelled_example
        assert not outcome.contradicted_the_system

    async def test_the_unlabelled_one_is_left_out_of_the_training_set(self, db_session):
        """The whole point of the flag. Treating an unknown as an implied hold
        would manufacture a label out of the queue's silence, and it would be
        indistinguishable from a real one by the time anyone trained on it."""
        hub = await _hub(db_session)
        labelled = await _order(db_session, hub)
        await _system_held(db_session, hub, labelled)
        unlabelled = await _order(db_session, hub)

        for order in (labelled, unlabelled):
            await apply_override(
                db_session, order_id=order.id, action="release",
                reason_code="customer_called", **USER,
            )
        await db_session.flush()

        rows = await labelled_overrides(db_session, hub_id=hub.id)
        assert [row.order_id for row in rows] == [labelled.id]

    async def test_agreeing_with_the_queue_is_not_a_contradiction(self, db_session):
        """A dispatcher reaching the same conclusion the queue did agrees with
        it and got there first. Counting that as a correction inflates the
        disagreement rate, which is the first number anyone judging the queue
        will look at."""
        hub = await _hub(db_session)
        # Already released, so a hold override is applicable - and the queue's
        # recorded decision was also a hold.
        order = await _order(db_session, hub, status=OrderStatus.queued)
        await _system_held(db_session, hub, order)

        outcome = await apply_override(
            db_session, order_id=order.id, action="hold",
            reason_code="hub_constraint", **USER,
        )
        await db_session.flush()

        assert outcome.override.system_action == "keep_holding"
        assert outcome.override.action == "hold"
        assert outcome.override.is_labelled_example
        assert not outcome.contradicted_the_system

        assert await labelled_overrides(db_session, hub_id=hub.id) == []
        both = await labelled_overrides(
            db_session, hub_id=hub.id, contradictions_only=False
        )
        assert len(both) == 1

    async def test_the_denominator_can_include_agreements(self, db_session):
        """`contradictions_only=False` is what a rate needs - the numerator on
        its own is a count, not a measure of anything."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        await _system_held(db_session, hub, order)
        await apply_override(
            db_session, order_id=order.id, action="release",
            reason_code="customer_called", **USER,
        )
        await db_session.flush()

        both = await labelled_overrides(db_session, hub_id=hub.id, contradictions_only=False)
        assert len(both) == 1


class TestTheVocabularyHasOneHome:
    async def test_the_migration_and_the_model_list_the_same_codes(self):
        """The CHECK constraint is written out in migration `0060` as literal
        SQL and cannot import the model, so the two can drift silently - and the
        symptom is a dispatcher picking a code the database rejects."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "m0060", "migrations/versions/0060_dispatcher_overrides.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module._REASON_CODES == REASON_CODES


class TestTheOverrideIsAppendOnly:
    async def test_the_database_refuses_to_edit_a_reason(self, db_session):
        """A reason editable after the outcome is known is not a label - and the
        tidying would look like housekeeping."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        outcome = await apply_override(
            db_session, order_id=order.id, action="release",
            reason_code="customer_called", **USER,
        )
        await db_session.commit()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text("UPDATE dispatcher_overrides SET reason_code = 'other' WHERE id = :id"),
                {"id": outcome.override.id},
            )
        await db_session.rollback()

    async def test_the_database_refuses_to_delete_an_override(self, db_session):
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        outcome = await apply_override(
            db_session, order_id=order.id, action="release",
            reason_code="customer_called", **USER,
        )
        await db_session.commit()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text("DELETE FROM dispatcher_overrides WHERE id = :id"),
                {"id": outcome.override.id},
            )
        await db_session.rollback()

    async def test_a_half_label_cannot_be_written(self, db_session):
        """Knowing the system's decision means having it. The constraint stops a
        row that claims a label and carries only one side of it."""
        hub = await _hub(db_session)
        await db_session.commit()

        with pytest.raises(Exception, match="label_is_whole"):
            await db_session.execute(
                text(
                    "INSERT INTO dispatcher_overrides (id, hub_id, order_id, "
                    "overridden_at, ops_user_id, ops_user_email, action, "
                    "reason_code, system_decision_known, system_action, "
                    "order_status_before) VALUES (:id, :hub, :order, :now, 'u', "
                    "'e@x.com', 'release', 'customer_called', true, NULL, 'held')"
                ),
                {"id": uuid.uuid4(), "hub": hub.id, "order": uuid.uuid4(), "now": OVERRODE_AT},
            )
        await db_session.rollback()


class TestTheOrderActuallyMoves:
    async def test_a_release_takes_the_order_out_of_the_hold_queue(self, db_session):
        hub = await _hub(db_session)
        order = await _order(db_session, hub, status=OrderStatus.held)

        outcome = await apply_override(
            db_session, order_id=order.id, action="release",
            reason_code="customer_called", **USER,
        )

        assert order.status == OrderStatus.queued
        assert outcome.override.order_status_before == "held"

    async def test_a_hold_puts_a_released_order_back(self, db_session):
        hub = await _hub(db_session)
        order = await _order(db_session, hub, status=OrderStatus.queued)

        await apply_override(
            db_session, order_id=order.id, action="hold",
            reason_code="hub_constraint", **USER,
        )

        assert order.status == OrderStatus.held

    async def test_an_assigned_order_cannot_be_held_back_from_here(self, db_session):
        """A driver has an offer in front of them by then. Retracting it is a
        driver-facing operation with a consequence, not a queue decision, and one
        button doing both would make the quiet case indistinguishable from the
        loud one."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub, status=OrderStatus.assigned)

        with pytest.raises(OverrideRefused, match="applies to"):
            await apply_override(
                db_session, order_id=order.id, action="hold",
                reason_code="hub_constraint", **USER,
            )

    async def test_an_order_that_moved_since_the_screen_loaded_is_refused_readably(
        self, db_session
    ):
        hub = await _hub(db_session)
        order = await _order(db_session, hub, status=OrderStatus.delivered)

        with pytest.raises(OverrideRefused, match="look at it again"):
            await apply_override(
                db_session, order_id=order.id, action="release",
                reason_code="customer_called", **USER,
            )

    async def test_nothing_is_written_when_the_override_is_refused(self, db_session):
        """A status that moved with no row saying who moved it is the state CON-2
        exists to make impossible."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub, status=OrderStatus.held)

        with pytest.raises(OverrideRefused):
            await apply_override(
                db_session, order_id=order.id, action="release",
                reason_code="other", **USER,
            )

        assert order.status == OrderStatus.held
        assert await overrides_for_order(db_session, order_id=order.id) == []

    async def test_no_such_order_is_refused(self, db_session):
        with pytest.raises(OverrideRefused, match="No such order"):
            await apply_override(
                db_session, order_id=uuid.uuid4(), action="release",
                reason_code="customer_called", **USER,
            )


class TestItShowsUpInTheExplanation:
    """CON-3's "lands in the decision log", read from the console (`AGT-4`)."""

    async def test_the_override_appears_beside_the_systems_decision(self, db_session):
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        await _system_held(db_session, hub, order)
        await apply_override(
            db_session, order_id=order.id, action="release",
            reason_code="customer_called", **USER,
        )
        await db_session.flush()

        explanation = await explain_order(db_session, order_id=order.id)

        statements = [fact.statement for fact in explanation.facts]
        assert any(
            s == "still held - waiting for another order going the same way" for s in statements
        )
        assert any("dispatcher@lmxit.com" in s for s in statements)
        assert [f.at for f in explanation.facts] == sorted(f.at for f in explanation.facts)

    async def test_an_override_with_no_cycle_is_still_explained(self, db_session):
        """The regression this class exists for. `explain_order` returned early
        with "no cycle has run" before it read the overrides, so an order a human
        had acted on reported as having nothing recorded about it - a refusal the
        record contradicts, which is the mirror of the narration AGT-4 forbids.
        """
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        await apply_override(
            db_session, order_id=order.id, action="release",
            reason_code="customer_waiting_on_site", **USER,
        )
        await db_session.flush()

        explanation = await explain_order(db_session, order_id=order.id)

        assert explanation.is_explained, "a human decision is a decision"
        assert explanation.unexplained is None
        assert "waiting on site" in explanation.facts[0].statement.lower()

    async def test_the_note_is_shown_and_not_summarised(self, db_session):
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        await apply_override(
            db_session, order_id=order.id, action="release",
            reason_code="other", note="Receiving bay closes at 3, driver is 5 min out",
            **USER,
        )
        await db_session.flush()

        explanation = await explain_order(db_session, order_id=order.id)

        assert "Receiving bay closes at 3" in explanation.facts[0].statement

    async def test_every_override_in_sequence_is_kept(self, db_session):
        """Released, pulled back, released again is three decisions by up to
        three people, and only the sequence says that."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        for action, reason in (
            ("release", "customer_called"),
            ("hold", "hub_constraint"),
            ("release", "driver_going_that_way"),
        ):
            await apply_override(
                db_session, order_id=order.id, action=action, reason_code=reason, **USER,
            )
        await db_session.flush()

        rows = await overrides_for_order(db_session, order_id=order.id)
        assert [row.action for row in rows] == ["release", "hold", "release"]


class TestTheEndpoint:
    async def test_it_records_and_reports_the_disagreement(self, db_session):
        from app.api.routes import override_order
        from app.models.ops_user import VIEWER_ROLE
        from app.ops_auth.dependencies import AuthedOpsUser
        from app.schemas.reporting import OverrideRequest

        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        await _system_held(db_session, hub, order)

        view = await override_order(
            order_id=order.id,
            body=OverrideRequest(action="release", reason_code="customer_called"),
            session=db_session,
            ops=AuthedOpsUser(
                ops_user_id="u1", email="v@example.com", name="Viewer", role=VIEWER_ROLE
            ),
        )

        assert view.contradicted_the_system is True
        assert view.order_status_before == "held"
        assert view.order_status_after == "queued"
        assert view.reason_label == REASON_LABELS["customer_called"]
        assert view.by == "v@example.com"

    async def test_a_refusal_is_a_409_a_dispatcher_can_read(self, db_session):
        from fastapi import HTTPException

        from app.api.routes import override_order
        from app.models.ops_user import VIEWER_ROLE
        from app.ops_auth.dependencies import AuthedOpsUser
        from app.schemas.reporting import OverrideRequest

        hub = await _hub(db_session)
        order = await _order(db_session, hub, status=OrderStatus.delivered)

        with pytest.raises(HTTPException) as exc:
            await override_order(
                order_id=order.id,
                body=OverrideRequest(action="release", reason_code="customer_called"),
                session=db_session,
                ops=AuthedOpsUser(
                    ops_user_id="u1", email="v@example.com", name="V", role=VIEWER_ROLE
                ),
            )

        assert exc.value.status_code == 409
        assert "look at it again" in exc.value.detail

    async def test_the_reason_list_is_served_rather_than_duplicated(self, db_session):
        from app.api.routes import override_reasons
        from app.models.ops_user import VIEWER_ROLE
        from app.ops_auth.dependencies import AuthedOpsUser

        options = await override_reasons(
            _ops=AuthedOpsUser(
                ops_user_id="u1", email="v@example.com", name="V", role=VIEWER_ROLE
            )
        )

        assert [o.code for o in options] == list(REASON_CODES)
        assert [o.code for o in options if o.note_required] == list(
            REASON_CODES_REQUIRING_NOTE
        )

    async def test_it_is_open_to_any_ops_session_not_only_admins(self):
        """A dispatcher on a viewer account who cannot release an order when the
        customer calls cannot run a day. What makes that safe is the record, not
        the role: every override is attributed and append-only."""
        import inspect

        from app.api.routes import override_order
        from app.ops_auth.dependencies import get_current_ops_user

        dependency = inspect.signature(override_order).parameters["ops"].default
        assert dependency.dependency is get_current_ops_user

    async def test_the_row_is_still_written_by_the_endpoint_path(self, db_session):
        from app.api.routes import override_order
        from app.models.ops_user import VIEWER_ROLE
        from app.ops_auth.dependencies import AuthedOpsUser
        from app.schemas.reporting import OverrideRequest

        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        await override_order(
            order_id=order.id,
            body=OverrideRequest(action="release", reason_code="customer_called"),
            session=db_session,
            ops=AuthedOpsUser(
                ops_user_id="u1", email="v@example.com", name="V", role=VIEWER_ROLE
            ),
        )
        await db_session.flush()

        rows = await overrides_for_order(db_session, order_id=order.id)
        assert len(rows) == 1
        assert isinstance(rows[0], DispatcherOverride)


# ---------------------------------------------------------------------------
# The override takes effect
# ---------------------------------------------------------------------------
#
# It didn't. The endpoint never committed, so the override and the order's new
# status were rolled back as the response went out, while the dispatcher was
# told it had happened. And had it committed, dispatch reads its working set
# from the Redis hold queue, which the override never touched.


async def _override_via_endpoint(db_session, order, action, reason_code="customer_called"):
    from app.api.routes import override_order
    from app.models.ops_user import VIEWER_ROLE
    from app.ops_auth.dependencies import AuthedOpsUser
    from app.schemas.reporting import OverrideRequest

    return await override_order(
        order_id=order.id,
        body=OverrideRequest(action=action, reason_code=reason_code),
        session=db_session,
        ops=AuthedOpsUser(
            ops_user_id="u1", email="v@example.com", name="Viewer", role=VIEWER_ROLE
        ),
    )


class _NoAssignments(RouteOptimizationClient):
    """A planner that assigns nothing, so a test reads only the hold decisions."""

    engine_name = "spy"

    async def optimize(self, drivers, stops):
        return [], [stop.stop_id for stop in stops]


async def _held_at_one_shop(db_session, hub, count=1):
    """Orders queued for real - held in Postgres and in the Redis hold queue,
    with an hour left on their deadlines - at one shop. One on its own is what
    the queue holds: it waits for a partner."""
    orders = [await _order(db_session, hub, status=OrderStatus.held) for _ in range(count)]
    await db_session.commit()
    now = datetime.now(timezone.utc)
    for order in orders:
        await HoldQueueStore().add(
            str(hub.id),
            HeldOrder(
                order_id=str(order.id),
                shop_lat=30.27,
                shop_lng=-97.74,
                sla_tier="T2",
                hold_deadline=now + timedelta(hours=1),
                held_since=now,
            ),
        )
    return orders


async def _a_driver_on_shift(hub):
    fleet = FleetStateManager()
    driver_id = str(uuid.uuid4())
    await fleet.upsert_driver_state(
        DriverState(driver_id=driver_id, hub_id=str(hub.id), status="available", capacity_units=5)
    )
    await fleet.update_driver_location(
        DriverLocation(
            driver_id=driver_id,
            lat=30.26,
            lng=-97.73,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        hub_id=str(hub.id),
    )


async def _decisions(hub):
    plan = await DispatchOptimizerService(route_client=_NoAssignments()).plan_cycle(str(hub.id))
    return plan, {d.order_id: (d.action, d.reason) for d in plan.hold_decisions}


class TestTheOverrideIsSaved:
    async def test_the_override_and_the_status_survive_the_request(self, db_session):
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        await db_session.commit()
        order_id = order.id

        await _override_via_endpoint(db_session, order, "release")
        # Whatever the endpoint left uncommitted goes now.
        await db_session.rollback()

        status = await db_session.scalar(select(Order.status).where(Order.id == order_id))
        recorded = await db_session.scalar(
            select(func.count())
            .select_from(DispatcherOverride)
            .where(DispatcherOverride.order_id == order_id)
        )
        assert status == OrderStatus.queued
        assert recorded == 1

    async def test_a_release_asks_for_a_cycle_at_once(self, db_session, published):
        """So the customer who called isn't waiting on the next event or the
        five-minute sweep."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)

        await _override_via_endpoint(db_session, order, "release")

        assert published == [(str(hub.id), "order_released")]

    async def test_a_hold_does_not(self, db_session, published):
        hub = await _hub(db_session)
        order = await _order(db_session, hub, status=OrderStatus.queued)

        await _override_via_endpoint(db_session, order, "hold", reason_code="hub_constraint")

        assert published == []


class TestDispatchActsOnIt:
    async def test_the_next_cycle_releases_what_the_dispatcher_released(
        self, db_session, real_redis_client
    ):
        hub = await _hub(db_session)
        (released,) = await _held_at_one_shop(db_session, hub)
        await _a_driver_on_shift(hub)
        _, before = await _decisions(hub)
        assert before[str(released.id)] == ("keep_holding", "waiting_for_cluster_mate")

        await _override_via_endpoint(db_session, released, "release")
        plan, after = await _decisions(hub)

        assert after[str(released.id)] == ("release", "dispatcher_released")
        assert plan.released_order_ids == [str(released.id)]

    async def test_a_hold_hands_it_back_to_the_queues_own_rules(
        self, db_session, real_redis_client
    ):
        hub = await _hub(db_session)
        (order,) = await _held_at_one_shop(db_session, hub)
        await _a_driver_on_shift(hub)

        await _override_via_endpoint(db_session, order, "release")
        await _override_via_endpoint(db_session, order, "hold", reason_code="hub_constraint")
        plan, after = await _decisions(hub)

        assert after[str(order.id)] == ("keep_holding", "waiting_for_cluster_mate")
        assert plan.released_order_ids == []


class TestTheLabelIsTheQueuesOwnView:
    async def test_a_hold_agreeing_with_the_queues_keep_holding_is_not_a_contradiction(
        self, db_session
    ):
        """Real cycles record "keep_holding" where an override says "hold", and
        both mean the order waits. Compared raw, every agreeing hold counted as
        a disagreement."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub, status=OrderStatus.queued)
        await _system_held(
            db_session, hub, order, action="keep_holding", reason="waiting_for_cluster_mate"
        )

        outcome = await apply_override(
            db_session, order_id=order.id, action="hold",
            reason_code="hub_constraint", **USER,
        )

        assert outcome.override.system_action == "keep_holding"
        assert not outcome.contradicted_the_system

    async def test_the_cycle_that_carried_out_a_release_is_not_cited(self, db_session):
        """After a release, the next cycle records the queue following the
        dispatcher. A hold after that reverses the dispatcher's own call, so
        the label cites the last decision the queue made for itself."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub, status=OrderStatus.queued)
        judged = await _system_held(
            db_session, hub, order, action="keep_holding", reason="waiting_for_cluster_mate"
        )
        await _system_held(
            db_session, hub, order, action="release", reason="dispatcher_released",
            at=DECIDED_AT + timedelta(minutes=5),
        )

        outcome = await apply_override(
            db_session, order_id=order.id, action="hold",
            reason_code="hub_constraint", **USER,
        )

        assert outcome.override.cited_snapshot_id == judged.id
        assert outcome.override.system_reason == "waiting_for_cluster_mate"
        assert not outcome.contradicted_the_system
