"""M2's label: did being late actually cost anything?

`MODEL_AND_DATA_BRIEF.md` §M2 predicts P(a consequence occurs | this order is
late), and the label is six consequence types **or silence**. Silence is half
the value - an urgency signal that only ever fires positive cannot price
anything.

The test that matters most here is the one asserting an unresolved order
labels as None rather than 0. Training unresolved orders as negatives is the
easiest way to make this model look good and be wrong: it would learn that
lateness is usually free, because most negatives would be orders nobody had
finished watching.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.client import Client
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.record import (
    CONSEQUENCES,
    close_consequence_windows,
    consequence_label,
    label_counts,
    late_orders_awaiting_judgement,
    record_consequence,
    record_delivery_outcome,
    record_silence,
    supersede_outcome,
)
from app.record.consequences import (
    CONSEQUENCE_COMPETITOR,
    CONSEQUENCE_CREDIT,
    CONSEQUENCE_ESCALATION,
    CONSEQUENCE_REORDER_GAP,
    DEFAULT_CONSEQUENCE_WINDOW,
    SILENCE,
)
from app.record.outcomes import outcomes_for
from app.sla.commitment import Commitment

pytestmark = pytest.mark.integration

PROMISED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
NOW = PROMISED + timedelta(days=30)


async def _hub_and_client(db_session):
    hub = Hub(id=uuid.uuid4(), name="M2 Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="M2 Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    return hub, client


async def _delivered(db_session, hub, client, *, late: bool, when=None) -> Order:
    delivered_at = when or (PROMISED + timedelta(minutes=45) if late else PROMISED - timedelta(minutes=5))
    order = Order(
        hub_id=hub.id, client_id=client.id, external_order_ref=f"PO-{uuid.uuid4().hex[:6]}",
        source_system="flat_file", raw_payload={}, sla_tier="T2",
        status=OrderStatus.delivered, requested_at=PROMISED - timedelta(hours=2),
        delivered_at=delivered_at,
    )
    db_session.add(order)
    await db_session.flush()
    await record_delivery_outcome(
        db_session, order, Commitment(promised_delivery_by=PROMISED, source="lmx")
    )
    return order


class TestTheLabel:
    async def test_an_observed_consequence_labels_one(self, db_session):
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=True)

        await record_consequence(
            db_session, order, CONSEQUENCE_ESCALATION,
            occurred_at=PROMISED + timedelta(hours=1),
            detail="Shop manager rang dispatch",
        )

        assert await consequence_label(db_session, order) == 1

    async def test_silence_labels_zero(self, db_session):
        """Half the value of the model. Without negatives there is nothing to
        calibrate a probability against."""
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=True)

        await record_silence(db_session, order, now=NOW)

        assert await consequence_label(db_session, order) == 0

    async def test_an_unresolved_order_is_none_not_zero(self, db_session):
        """The test this file exists for.

        Treating "nobody has looked yet" as "nothing happened" would teach the
        model that lateness is usually free, because most of the negatives
        would be orders still inside their window.
        """
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=True)

        assert await consequence_label(db_session, order) is None

    @pytest.mark.parametrize("kind", CONSEQUENCES)
    async def test_all_six_types_are_recordable(self, db_session, kind):
        """Two of these - competitor-sourced and reorder gap - had no
        representation anywhere in the codebase before this."""
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=True)

        entry = await record_consequence(
            db_session, order, kind, occurred_at=PROMISED + timedelta(days=1)
        )
        assert entry.values["consequence"] == kind
        assert await consequence_label(db_session, order) == 1

    async def test_an_invented_consequence_is_refused(self, db_session):
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=True)

        with pytest.raises(ValueError, match="kind must be one of"):
            await record_consequence(
                db_session, order, "customer_seemed_annoyed", occurred_at=NOW
            )

    async def test_a_credit_carries_its_amount(self, db_session):
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=True)

        entry = await record_consequence(
            db_session, order, CONSEQUENCE_CREDIT,
            occurred_at=PROMISED + timedelta(days=2), amount_cents=4200,
        )
        assert entry.values["amount_cents"] == 4200


class TestClosingWindows:
    async def test_a_late_order_past_its_window_is_judged_silent(self, db_session):
        hub, client = await _hub_and_client(db_session)
        await _delivered(db_session, hub, client, late=True)

        closed = await close_consequence_windows(db_session, hub_id=hub.id, now=NOW)

        assert closed == 1

    async def test_an_order_still_inside_its_window_is_left_alone(self, db_session):
        hub, client = await _hub_and_client(db_session)
        await _delivered(db_session, hub, client, late=True)

        just_after = PROMISED + timedelta(days=1)
        assert await close_consequence_windows(db_session, hub_id=hub.id, now=just_after) == 0

    async def test_an_on_time_order_is_never_judged(self, db_session):
        """M2 is conditioned on lateness. An on-time delivery is not a
        negative example - it is outside the population entirely."""
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=False)

        assert await close_consequence_windows(db_session, hub_id=hub.id, now=NOW) == 0
        assert await consequence_label(db_session, order) is None

    async def test_an_order_that_already_has_a_consequence_is_not_overwritten(self, db_session):
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=True)
        await record_consequence(
            db_session, order, CONSEQUENCE_COMPETITOR, occurred_at=PROMISED + timedelta(days=3)
        )

        assert await close_consequence_windows(db_session, hub_id=hub.id, now=NOW) == 0
        assert await consequence_label(db_session, order) == 1

    async def test_the_sweep_is_idempotent(self, db_session):
        hub, client = await _hub_and_client(db_session)
        await _delivered(db_session, hub, client, late=True)

        first = await close_consequence_windows(db_session, hub_id=hub.id, now=NOW)
        second = await close_consequence_windows(db_session, hub_id=hub.id, now=NOW)
        assert (first, second) == (1, 0)

    async def test_the_window_it_was_judged_under_is_stored(self, db_session):
        """The brief does not state a window, so every silence carries the one
        it was judged under - a later decision can re-derive rather than
        silently reinterpret."""
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=True)
        await record_silence(db_session, order, window=timedelta(days=30), now=NOW)

        entry = (await outcomes_for(db_session, subject_id=order.id))[-1]
        assert entry.values["window_days"] == 30
        assert entry.values["consequence"] == SILENCE

    async def test_a_shorter_window_judges_sooner(self, db_session):
        hub, client = await _hub_and_client(db_session)
        await _delivered(db_session, hub, client, late=True)

        two_days_later = PROMISED + timedelta(days=3)
        closed = await close_consequence_windows(
            db_session, hub_id=hub.id, window=timedelta(days=2), now=two_days_later
        )
        assert closed == 1


class TestCorrections:
    async def test_a_consequence_later_found_to_be_something_else(self, db_session):
        """A silence recorded, then the customer's reorder never came. The
        ledger's supersede path carries it without rewriting the original."""
        hub, client = await _hub_and_client(db_session)
        order = await _delivered(db_session, hub, client, late=True)
        silence = await record_silence(db_session, order, now=NOW)

        await supersede_outcome(
            db_session, silence,
            values={"consequence": CONSEQUENCE_REORDER_GAP},
            reason="Customer's monthly reorder did not arrive; they sourced elsewhere",
        )

        assert await consequence_label(db_session, order) == 1
        assert len(await outcomes_for(db_session, subject_id=order.id)) == 3


class TestReadiness:
    async def test_the_count_reports_against_the_band_the_brief_states(self, db_session):
        """500-1,000, from `DATA_NEED_BRIEF.md` §4.2 - and deliberately not 626,
        which is §4.3's Wilson sizing for M5's modality false-positive rate.
        This function reported against 626 once. Both are a few hundred labels
        from the same document and 626 sits inside the band, so the error was
        invisible; the assertion is here to keep it that way."""
        hub, client = await _hub_and_client(db_session)
        for _ in range(3):
            order = await _delivered(db_session, hub, client, late=True)
            await record_consequence(
                db_session, order, CONSEQUENCE_ESCALATION, occurred_at=NOW
            )
        silent = await _delivered(db_session, hub, client, late=True)
        await record_silence(db_session, silent, now=NOW)

        counts = await label_counts(db_session, hub_id=hub.id)

        assert counts["observed_consequences"] == 3
        assert counts["silences"] == 1
        assert counts["labelled_total"] == 4
        assert counts["required_range_for_m2"] == (500, 1000)
        assert counts["at_band_minimum"] is False
        assert counts["at_band_target"] is False
        assert counts["shortfall_to_minimum"] == 497
        assert counts["shortfall_to_target"] == 997
        assert "required_for_m2" not in counts, "626 must not come back"
        assert counts["by_type"][CONSEQUENCE_ESCALATION] == 3

    async def test_pending_orders_are_listed_so_somebody_can_chase_them(self, db_session):
        hub, client = await _hub_and_client(db_session)
        late = await _delivered(db_session, hub, client, late=True)
        await _delivered(db_session, hub, client, late=False)

        pending = await late_orders_awaiting_judgement(db_session, hub_id=hub.id, now=NOW)

        assert [o.id for o in pending] == [late.id]

    async def test_the_default_window_is_declared_not_hidden(self):
        """It is a placeholder and the brief owes a real number. Asserting it
        here means changing it is a visible decision rather than a quiet edit."""
        assert DEFAULT_CONSEQUENCE_WINDOW == timedelta(days=14)
