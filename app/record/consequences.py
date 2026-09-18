"""Did being late actually cost anything? (`M2`'s label.)

`MODEL_AND_DATA_BRIEF.md` §M2 predicts *P(a consequence occurs | this order is
late)*, and names the label: escalation call, credit issued, part returned,
order cancelled, competitor-sourced, reorder gap - **or silence**. Silence when
late is the label that says the flag was soft, and it is half the value of the
model: an urgency signal that only ever fires positive cannot price anything.

**Why this is worth building before there is volume to use it.** A consequence
not captured when it happens is gone. Nobody can reconstruct, months later,
whether a customer rang up angry about a particular delivery, or quietly bought
the part elsewhere. `DATA_NEED_BRIEF.md` §4.2 puts `M2` at 500-1,000 observed
consequences - 62,500 to 125,000 deliveries at the rate it assumes, 21 to 41
months at the design partner alone - and that clock does not start when the
deliveries start, it starts when the *labels* start. Until then the drops
accumulate and the labels do not.

Two of the six had no representation anywhere in this codebase before this
module: competitor-sourced and reorder gap. Credits existed in billing,
cancellations in the order state machine, escalation in COD notifications -
none of it joined to "this order was late", which is the only thing that makes
any of it a label.

**Silence is recorded, never inferred.** An absent row is ambiguous between
"nothing happened" and "nobody looked", and treating the second as the first
would train the model that lateness is usually free. So a window closes
explicitly and writes a `silence` entry, which means the label set is
self-describing: every late order either has a consequence or has been checked.

**These are outcomes, so they live in REC-3's ledger.** Same append-only
guarantee, same supersede path for a consequence later found to be something
else, same optional link to the decision that caused the lateness. A second
table would have been a second set of those properties to keep true.

**What this does NOT do: make `M2` trainable.** The brief is explicit that the
label *"requires the randomised hold arm (`EXP-1`). Unobtainable by watching."*
Lateness that happens for non-random reasons is confounded with whatever caused
it, so consequences gathered without the arm describe the orders that tend to
run late rather than the effect of running late. This captures the label. `EXP-1`
is what makes it mean something.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.outcome_entry import KIND_DELIVERED, SUBJECT_ORDER, OutcomeEntry
from app.record.outcomes import record_outcome

# The six, as the brief names them, plus silence.
CONSEQUENCE_ESCALATION = "escalation_call"
CONSEQUENCE_CREDIT = "credit_issued"
CONSEQUENCE_RETURNED = "part_returned"
CONSEQUENCE_CANCELLED = "order_cancelled"
CONSEQUENCE_COMPETITOR = "competitor_sourced"
CONSEQUENCE_REORDER_GAP = "reorder_gap"
SILENCE = "silence"

# `DATA_NEED_BRIEF.md` §4.2. A band, quoted as a band. Not 626 - that is §4.3's
# Wilson sizing for M5's modality false-positive rate and belongs to a different
# model entirely.
M2_BAND_MINIMUM = 500
M2_BAND_TARGET = 1000

CONSEQUENCES = (
    CONSEQUENCE_ESCALATION,
    CONSEQUENCE_CREDIT,
    CONSEQUENCE_RETURNED,
    CONSEQUENCE_CANCELLED,
    CONSEQUENCE_COMPETITOR,
    CONSEQUENCE_REORDER_GAP,
)

# What each one means to the person recording it. The wording is the label's
# definition as far as a dispatcher is concerned - `competitor_sourced` and
# `order_cancelled` both end with no delivery, and which one somebody picks
# depends entirely on how the choice is worded on the screen.
CONSEQUENCE_LABELS: dict[str, str] = {
    CONSEQUENCE_ESCALATION: "The customer called to chase it",
    CONSEQUENCE_CREDIT: "We issued a credit",
    CONSEQUENCE_RETURNED: "The part came back",
    CONSEQUENCE_CANCELLED: "The order was cancelled",
    CONSEQUENCE_COMPETITOR: "They sourced it somewhere else",
    CONSEQUENCE_REORDER_GAP: "They stopped ordering",
}

# How long after a late delivery we keep watching before calling it silence.
#
# **The brief does not state this, and it should.** It is not a tuning knob -
# it defines the label. Too short and a reorder gap, which is visible only when
# the customer's next order does not come, is recorded as silence; too long and
# the model is trained on consequences that had nothing to do with the
# delivery. The six types plausibly want different windows - an escalation call
# arrives in hours, a reorder gap takes weeks - and collapsing them to one
# number is a simplification, not an answer.
#
# Fourteen days is a placeholder wide enough for the slow signals and narrow
# enough to attribute. It needs somebody who knows the trade to set it, and
# until they do, every silence label carries the window it was judged under so
# a later change can re-derive rather than guess.
DEFAULT_CONSEQUENCE_WINDOW = timedelta(days=14)


async def record_consequence(
    session: AsyncSession,
    order: Order,
    kind: str,
    *,
    occurred_at: datetime,
    detail: str | None = None,
    amount_cents: int | None = None,
    decision_snapshot_id=None,
) -> OutcomeEntry:
    """One observed consequence of a late delivery.

    Deliberately takes the order rather than an id: a consequence is only a
    label in relation to a delivery, and requiring the caller to hold one makes
    it harder to record a consequence floating free of the thing it is about.
    """
    if kind not in CONSEQUENCES:
        raise ValueError(f"kind must be one of {CONSEQUENCES}, got {kind!r}")

    values: dict = {"consequence": kind}
    if detail:
        values["detail"] = detail[:500]
    if amount_cents is not None:
        values["amount_cents"] = amount_cents

    return await record_outcome(
        session,
        hub_id=order.hub_id,
        subject_type=SUBJECT_ORDER,
        subject_id=order.id,
        # Reuses the ledger's `disputed` slot rather than adding a kind per
        # consequence: the ledger's kinds describe what sort of fact a row is,
        # and all seven of these are the same sort - an observation about how a
        # delivery landed with the customer. Which one it was lives in `values`.
        kind="disputed",
        occurred_at=occurred_at,
        values=values,
        decision_snapshot_id=decision_snapshot_id,
    )


async def record_silence(
    session: AsyncSession,
    order: Order,
    *,
    window: timedelta = DEFAULT_CONSEQUENCE_WINDOW,
    now: datetime | None = None,
) -> OutcomeEntry:
    """Nothing happened, and we looked.

    The window is stored on the entry. When somebody eventually sets a real
    one - or different ones per consequence type - the existing labels can be
    re-derived against it instead of being thrown away or, worse, silently
    reinterpreted.
    """
    now = now or datetime.now(timezone.utc)
    return await record_outcome(
        session,
        hub_id=order.hub_id,
        subject_type=SUBJECT_ORDER,
        subject_id=order.id,
        kind="disputed",
        occurred_at=now,
        values={
            "consequence": SILENCE,
            "window_days": window.days,
            "judged_at": now.isoformat(),
        },
    )


async def _consequence_entries(session: AsyncSession, subject_id) -> list[OutcomeEntry]:
    entries = list(
        await session.scalars(
            select(OutcomeEntry).where(
                OutcomeEntry.subject_id == subject_id,
                OutcomeEntry.kind == "disputed",
            )
        )
    )
    superseded = {e.supersedes for e in entries if e.supersedes is not None}
    return [e for e in entries if e.id not in superseded]


async def consequence_label(session: AsyncSession, order: Order) -> int | None:
    """`M2`'s target for one order: 1, 0, or not yet known.

    1 - a consequence was observed
    0 - the window closed and none was
    None - still inside the window, or the order was never late

    None is not a zero. Training on unresolved orders as negatives is the
    single easiest way to make this model look good and be wrong: it would
    learn that lateness is usually free, because most of the labels would be
    orders nobody had finished watching.
    """
    entries = await _consequence_entries(session, order.id)
    if any(e.values.get("consequence") in CONSEQUENCES for e in entries):
        return 1
    if any(e.values.get("consequence") == SILENCE for e in entries):
        return 0
    return None


async def late_orders_awaiting_judgement(
    session: AsyncSession,
    *,
    hub_id,
    window: timedelta = DEFAULT_CONSEQUENCE_WINDOW,
    now: datetime | None = None,
) -> list[Order]:
    """Late deliveries whose window has closed and that nobody has judged.

    "Late" comes from the REC-3 delivered outcome rather than recomputed here.
    That outcome stored the promise it was measured against, so a delivery
    stays judged by the terms that applied to it even after the client's SLA
    terms change - which they do.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - window

    late_ids = [
        entry.subject_id
        for entry in await session.scalars(
            select(OutcomeEntry).where(
                OutcomeEntry.hub_id == hub_id,
                OutcomeEntry.kind == KIND_DELIVERED,
                OutcomeEntry.occurred_at <= cutoff,
            )
        )
        if entry.values.get("on_time") is False
    ]
    if not late_ids:
        return []

    judged = {
        entry.subject_id
        for entry in await session.scalars(
            select(OutcomeEntry).where(
                OutcomeEntry.subject_id.in_(late_ids),
                OutcomeEntry.kind == "disputed",
            )
        )
    }
    pending = [oid for oid in late_ids if oid not in judged]
    if not pending:
        return []
    return list(await session.scalars(select(Order).where(Order.id.in_(pending))))


async def close_consequence_windows(
    session: AsyncSession,
    *,
    hub_id,
    window: timedelta = DEFAULT_CONSEQUENCE_WINDOW,
    now: datetime | None = None,
) -> int:
    """Record silence for every late order whose window has closed unjudged.

    Run on a schedule. Without it the label set fills with positives only -
    somebody always records the angry phone call, and nobody ever records the
    twelve deliveries that were late and fine.
    """
    now = now or datetime.now(timezone.utc)
    pending = await late_orders_awaiting_judgement(
        session, hub_id=hub_id, window=window, now=now
    )
    for order in pending:
        await record_silence(session, order, window=window, now=now)
    return len(pending)


async def label_counts(session: AsyncSession, *, hub_id) -> dict:
    """How close the label set is to being usable, in the brief's own terms.

    `DATA_NEED_BRIEF.md` §4.2 puts the requirement at **500-1,000 observed
    consequences**. That is a stated band, not a derived figure, so this reports
    against both ends rather than picking a point target between them and
    lending it a precision the source does not have.

    **It is specifically not 626.** That number is §4.3's: a Wilson interval
    sizing `M5`'s modality false-positive rate to ±2 points, per modality. It
    has nothing to do with how many labels train a classifier. An earlier
    version of this function reported against it, and the mistake is easy to
    make - both are "a few hundred labels", both come from the same document,
    and 626 sits inside 500-1,000 so nothing looked wrong. That is why the
    citation is in the code rather than in somebody's memory.
    """
    entries = list(
        await session.scalars(
            select(OutcomeEntry).where(
                OutcomeEntry.hub_id == hub_id, OutcomeEntry.kind == "disputed"
            )
        )
    )
    superseded = {e.supersedes for e in entries if e.supersedes is not None}
    live = [e for e in entries if e.id not in superseded]

    observed = sum(1 for e in live if e.values.get("consequence") in CONSEQUENCES)
    silent = sum(1 for e in live if e.values.get("consequence") == SILENCE)
    by_type = {
        kind: sum(1 for e in live if e.values.get("consequence") == kind)
        for kind in CONSEQUENCES
    }
    return {
        "observed_consequences": observed,
        "silences": silent,
        "labelled_total": observed + silent,
        "by_type": by_type,
        "required_range_for_m2": (M2_BAND_MINIMUM, M2_BAND_TARGET),
        # No key called "ready": the brief states a band and declines to say
        # where inside it the model becomes trainable, so this reports the two
        # ends and leaves the judgement to whoever has to defend it.
        "at_band_minimum": observed >= M2_BAND_MINIMUM,
        "at_band_target": observed >= M2_BAND_TARGET,
        "shortfall_to_minimum": max(M2_BAND_MINIMUM - observed, 0),
        "shortfall_to_target": max(M2_BAND_TARGET - observed, 0),
    }
