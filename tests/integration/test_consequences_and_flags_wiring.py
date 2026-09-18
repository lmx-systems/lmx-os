"""REC-2 and REC-4: the last two unwired rows in the record layer.

From `docs/ROADMAP_AUDIT_2026-09.md`. `record_consequence` had no caller, so no
consequence was ever recorded; `run_linkage_detectors` had no caller, so no flag
was ever raised. Both read `BUILT`.

**The order mattered.** `close_consequence_windows` records *silence* - "nothing
happened" - for every late order nobody judged inside the window. Wiring that
scheduler while there was still no way to judge one would have labelled every
late delivery as silence: false labels, in an append-only ledger,
indistinguishable from true ones by the time anyone trained on them. So the
judging surface is built here too, and `TestTheScheduleWouldHaveLied` is the
class that says why.

REC-4 has the same shape in a quieter key: a detector that raises flags into a
table nobody opens is not better than a detector that never runs.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.client import Client
from app.models.hub import Hub
from app.models.linkage_flag import KIND_REPEAT_VISIT, LinkageFlag
from app.models.ops_user import VIEWER_ROLE
from app.models.order import Order, OrderStatus
from app.models.outcome_entry import KIND_DELIVERED, SUBJECT_ORDER, OutcomeEntry
from app.ops_auth.dependencies import AuthedOpsUser
from app.record.consequences import (
    CONSEQUENCE_ESCALATION,
    CONSEQUENCE_LABELS,
    CONSEQUENCES,
    DEFAULT_CONSEQUENCE_WINDOW,
    SILENCE,
    close_consequence_windows,
    label_counts,
    late_orders_awaiting_judgement,
)
from app.record.outcomes import record_outcome
from app.schemas.reporting import ConsequenceRequest

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - DEFAULT_CONSEQUENCE_WINDOW - timedelta(days=1)

OPS = AuthedOpsUser(
    ops_user_id="u1", email="dispatcher@lmxit.com", name="Dispatcher", role=VIEWER_ROLE
)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Consequence Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    hub._client_id = client.id
    return hub


async def _late_delivery(db_session, hub, *, delivered_at=LONG_AGO, on_time=False) -> Order:
    """An order delivered, with the REC-3 outcome a real delivery now writes."""
    order = Order(
        hub_id=hub.id, client_id=hub._client_id,
        external_order_ref=f"PO-{uuid.uuid4().hex[:6]}", source_system="flat_file",
        raw_payload={}, sla_tier="T2", status=OrderStatus.delivered,
        requested_at=delivered_at - timedelta(hours=3),
        promised_at=delivered_at - timedelta(hours=1),
        delivered_at=delivered_at,
    )
    db_session.add(order)
    await db_session.flush()

    await record_outcome(
        db_session,
        hub_id=hub.id,
        subject_type=SUBJECT_ORDER,
        subject_id=order.id,
        kind=KIND_DELIVERED,
        occurred_at=delivered_at,
        values={
            "delivered_at": delivered_at.isoformat(),
            "promised_delivery_by": (delivered_at - timedelta(hours=1)).isoformat(),
            "commitment_source": "tier_term",
            "sla_tier": "T2",
            "lateness_seconds": 3600.0 if not on_time else -3600.0,
            "on_time": on_time,
        },
    )
    await db_session.flush()
    return order


class TestTheWorklistExists:
    async def test_a_late_delivery_past_its_window_needs_judging(self, db_session):
        hub = await _hub(db_session)
        order = await _late_delivery(db_session, hub)

        pending = await late_orders_awaiting_judgement(db_session, hub_id=hub.id, now=NOW)

        assert [o.id for o in pending] == [order.id]

    async def test_an_on_time_delivery_is_not_on_it(self, db_session):
        """A consequence is a consequence *of lateness*. Judging on-time
        deliveries would fill the label set with rows that say nothing about the
        thing being measured."""
        hub = await _hub(db_session)
        await _late_delivery(db_session, hub, on_time=True)

        assert await late_orders_awaiting_judgement(db_session, hub_id=hub.id, now=NOW) == []

    async def test_a_recent_late_delivery_is_not_yet_due(self, db_session):
        """Inside the window there is still time for something to happen. Asking
        a dispatcher to judge it today produces a silence that is really a
        not-yet."""
        hub = await _hub(db_session)
        await _late_delivery(db_session, hub, delivered_at=NOW - timedelta(days=1))

        assert await late_orders_awaiting_judgement(db_session, hub_id=hub.id, now=NOW) == []

    async def test_the_endpoint_returns_it_with_how_late_it_was(self, db_session):
        from app.api.routes import late_orders

        hub = await _hub(db_session)
        await _late_delivery(db_session, hub)

        view = await late_orders(hub_id=hub.id, session=db_session, _ops=OPS)

        assert len(view) == 1
        assert view[0].minutes_late == 60


class TestADispatcherCanRecordWhatHappened:
    async def test_recording_a_consequence_lands_in_the_ledger(self, db_session):
        from app.api.routes import record_order_consequence

        hub = await _hub(db_session)
        order = await _late_delivery(db_session, hub)

        result = await record_order_consequence(
            order_id=order.id,
            body=ConsequenceRequest(kind=CONSEQUENCE_ESCALATION, detail="Called twice"),
            session=db_session,
            _ops=OPS,
        )

        assert result["consequence"] == CONSEQUENCE_ESCALATION
        counts = await label_counts(db_session, hub_id=hub.id)
        assert counts["observed_consequences"] == 1
        assert counts["silences"] == 0

    async def test_a_judged_order_leaves_the_worklist(self, db_session):
        from app.api.routes import record_order_consequence

        hub = await _hub(db_session)
        order = await _late_delivery(db_session, hub)
        await record_order_consequence(
            order_id=order.id,
            body=ConsequenceRequest(kind=CONSEQUENCE_ESCALATION),
            session=db_session,
            _ops=OPS,
        )

        assert await late_orders_awaiting_judgement(db_session, hub_id=hub.id, now=NOW) == []

    async def test_an_invented_consequence_is_refused(self, db_session):
        from fastapi import HTTPException

        from app.api.routes import record_order_consequence

        hub = await _hub(db_session)
        order = await _late_delivery(db_session, hub)

        with pytest.raises(HTTPException) as exc:
            await record_order_consequence(
                order_id=order.id,
                body=ConsequenceRequest(kind="customer_was_annoyed"),
                session=db_session,
                _ops=OPS,
            )
        assert exc.value.status_code == 422

    async def test_every_consequence_the_brief_names_has_a_label(self, db_session):
        """The wording *is* the label's definition as far as a dispatcher is
        concerned - `competitor_sourced` and `order_cancelled` both end with no
        delivery, and which one somebody picks depends on how the choice reads
        on the screen."""
        from app.api.routes import consequence_kinds

        options = await consequence_kinds(_ops=OPS)

        assert [o.code for o in options] == list(CONSEQUENCES)
        assert set(CONSEQUENCE_LABELS) == set(CONSEQUENCES)
        assert all(o.label for o in options)


class TestTheScheduleWouldHaveLied:
    """Why the surface had to be built before the scheduler was wired."""

    async def test_an_unjudged_late_order_becomes_a_silence(self, db_session):
        """The intended behaviour: somebody always records the angry phone call
        and nobody records the twelve deliveries that were late and fine, so the
        silences have to be recorded by something that is not a person."""
        hub = await _hub(db_session)
        await _late_delivery(db_session, hub)

        recorded = await close_consequence_windows(db_session, hub_id=hub.id, now=NOW)

        assert recorded == 1
        counts = await label_counts(db_session, hub_id=hub.id)
        assert counts["silences"] == 1
        assert counts["observed_consequences"] == 0

    async def test_a_judged_order_is_never_overwritten_with_silence(self, db_session):
        """The hazard, exactly. Had the scheduler been wired with no way to
        judge an order, every late delivery would have become a "nothing
        happened" label - false, permanent, and indistinguishable from a true
        one by the time anyone trained on it."""
        from app.api.routes import record_order_consequence

        hub = await _hub(db_session)
        order = await _late_delivery(db_session, hub)
        await record_order_consequence(
            order_id=order.id,
            body=ConsequenceRequest(kind=CONSEQUENCE_ESCALATION),
            session=db_session,
            _ops=OPS,
        )

        recorded = await close_consequence_windows(db_session, hub_id=hub.id, now=NOW)

        assert recorded == 0
        counts = await label_counts(db_session, hub_id=hub.id)
        assert counts["observed_consequences"] == 1
        assert counts["silences"] == 0

    async def test_running_it_twice_does_not_double_count(self, db_session):
        """It runs nightly, and a hub's late orders do not stop being late."""
        hub = await _hub(db_session)
        await _late_delivery(db_session, hub)

        first = await close_consequence_windows(db_session, hub_id=hub.id, now=NOW)
        second = await close_consequence_windows(db_session, hub_id=hub.id, now=NOW)

        assert (first, second) == (1, 0)
        assert (await label_counts(db_session, hub_id=hub.id))["silences"] == 1

    async def test_the_silence_carries_the_window_it_was_judged_under(self, db_session):
        """The window is a placeholder nobody has set, so every silence records
        which one produced it - a later change re-derives rather than guesses."""
        hub = await _hub(db_session)
        await _late_delivery(db_session, hub)
        await close_consequence_windows(db_session, hub_id=hub.id, now=NOW)

        entry = await db_session.scalar(
            select(OutcomeEntry).where(OutcomeEntry.kind == "disputed")
        )
        assert entry.values["consequence"] == SILENCE
        assert entry.values.get("window_days") == DEFAULT_CONSEQUENCE_WINDOW.days


class TestTheLinkageFlagsAreReadable:
    async def _flag(self, db_session, hub, *, kind=KIND_REPEAT_VISIT, detail="A dock visited twice today"):
        flag = LinkageFlag(
            hub_id=hub.id, kind=kind, fingerprint=uuid.uuid4().hex,
            subjects={"order_ids": [str(uuid.uuid4())]}, detail=detail,
            detected_at=NOW - timedelta(hours=1),
        )
        db_session.add(flag)
        await db_session.flush()
        return flag

    async def test_open_flags_are_served_to_the_console(self, db_session):
        from app.api.routes import linkage_flags

        hub = await _hub(db_session)
        await self._flag(db_session, hub)

        view = await linkage_flags(hub_id=hub.id, session=db_session, _ops=OPS)

        assert len(view) == 1
        assert view[0].detail == "A dock visited twice today"
        assert view[0].subjects

    async def test_resolving_records_that_somebody_looked(self, db_session):
        """Recorded rather than deleted. A dismissed flag is evidence that a
        person considered the case, and deleting it would let the detector raise
        the same question again tomorrow."""
        from app.api.routes import resolve_linkage_flag

        hub = await _hub(db_session)
        flag = await self._flag(db_session, hub)

        view = await resolve_linkage_flag(
            flag_id=flag.id, note="Two genuine orders", session=db_session, _ops=OPS
        )

        assert view.resolved_at is not None
        assert view.resolution_note == "Two genuine orders"
        assert await db_session.scalar(
            select(func.count()).select_from(LinkageFlag)
        ) == 1

    async def test_a_resolved_flag_leaves_the_queue(self, db_session):
        from app.api.routes import linkage_flags, resolve_linkage_flag

        hub = await _hub(db_session)
        flag = await self._flag(db_session, hub)
        await resolve_linkage_flag(flag_id=flag.id, session=db_session, _ops=OPS)

        assert await linkage_flags(hub_id=hub.id, session=db_session, _ops=OPS) == []

    async def test_another_hubs_flags_are_not_shown(self, db_session):
        from app.api.routes import linkage_flags

        hub = await _hub(db_session)
        elsewhere = await _hub(db_session)
        await self._flag(db_session, elsewhere)

        assert await linkage_flags(hub_id=hub.id, session=db_session, _ops=OPS) == []


class TestTheNightlyTickRunsBoth:
    async def test_the_scheduler_calls_them(self):
        """Both were built and scheduled nowhere. Asserting the wiring here
        rather than only in a scheduler test, because the scheduler test would
        pass with either call removed."""
        import inspect

        from app.learning_loop import scheduler

        source = inspect.getsource(scheduler)
        assert "close_consequence_windows(session, hub_id=hub_id)" in source
        assert "run_linkage_detectors(session, hub_id=hub_id)" in source

    async def test_each_failure_is_isolated_from_the_others(self):
        """A dwell refresh must not cost a hub its rule proposals, a consequence
        close must not cost it the linkage flags, and so on. Four independent
        try blocks rather than one."""
        import inspect

        from app.learning_loop import scheduler

        source = inspect.getsource(scheduler.LearningLoopScheduler.maybe_run_for_hub)
        assert source.count("except Exception") >= 4
