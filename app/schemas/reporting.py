"""The LMX Link scorecard as it leaves the API (docs/LMX_LINK_PLAN.md §3.4)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MeasurementView(BaseModel):
    name: str
    # The target as written in §3.4, carried alongside the number so a drifting
    # target and a drifting measurement cannot quietly diverge in someone's slide.
    target: str
    unit: str
    # Percentiles rather than a mean: an average entry time is dominated by the one
    # order somebody left open over lunch, and the target is about what entry
    # normally feels like.
    median: float | None
    p90: float | None
    sample_size: int
    # Set INSTEAD of the numbers, never alongside them. "We don't record this" and
    # "no data yet" are different problems and the text says which.
    not_measured: str | None


class LinkScorecardView(BaseModel):
    generated_at: datetime
    measurements: list[MeasurementView]


class RateView(BaseModel):
    """One proportion, with its arithmetic visible.

    `numerator`/`denominator` are part of the answer: "100% on time" means something
    entirely different at n=1 and n=400, and `is_thin` says which side of that a reader
    is looking at rather than leaving them to work it out.
    """

    name: str
    target: str
    numerator: int
    denominator: int
    percentage: float | None
    is_thin: bool
    not_measured: str | None


class OperationsScorecardView(BaseModel):
    """Descriptive analytics on captured ground truth (docs/ROADMAP.md I4).

    Before a pilot has run, the honest content of this is four `not_measured` reasons -
    which is a correct answer rather than an empty response, and the distinction the
    whole reporting vocabulary exists to preserve.
    """

    generated_at: datetime
    window_days: int
    window_start: datetime
    measurements: list[MeasurementView]
    rates: list[RateView]


class TierExposureView(BaseModel):
    """Credits for one tier, with the placeholder percentage that produced them.

    `credit_percent` is null when clients disagree about it - the report says so rather
    than picking one, because "what does this tier cost us" has no single answer then.
    """

    sla_tier: str
    credit_percent: int | None
    credit_cents: int
    breach_count: int
    delivered_count: int
    breach_rate_percent: float | None


class ClientExposureView(BaseModel):
    client_id: str
    client_name: str
    issued_cents: int
    accruing_cents: int
    total_cents: int


class CreditExposureView(BaseModel):
    """What service-level credits are costing (docs/ROADMAP.md W3, E11).

    `issued` is already on statements. `accruing` is delivered work not yet invoiced that
    would breach if it were - which is the half that matters, because credits are
    otherwise invisible until somebody runs billing.
    """

    generated_at: datetime
    window_days: int
    window_start: datetime
    issued_cents: int
    accruing_cents: int
    total_cents: int
    by_tier: list[TierExposureView]
    by_client: list[ClientExposureView]
    unassessable_orders: int
    unpriced_orders: int


class ExceptionItemView(BaseModel):
    """One thing worth a dispatcher's attention (docs/ROADMAP_1.5.md CON-4).

    `minutes_waiting` is named for what it is rather than scored. The thing that
    would rank these properly is `M2` - P(a consequence | this order is late) -
    and it needs 500-1,000 observed consequences that do not exist yet. An
    "urgency score" here would look like the model it is standing in for.
    """

    kind: str
    order_id: uuid.UUID
    client_id: uuid.UUID | None
    external_ref: str
    sla_tier: str | None
    minutes_waiting: float
    promised_at: datetime | None
    detail: str
    next_action: str


class ExceptionQueueView(BaseModel):
    """What ops should look at before the phone rings.

    Worst wait first. Empty is the correct and common answer, and it means
    nothing is outstanding rather than that nothing was checked - the counts
    make that legible.
    """

    generated_at: datetime
    counts: dict[str, int]
    worst_wait_minutes: float
    items: list[ExceptionItemView]


class DecisionFactView(BaseModel):
    """One thing the decision log says, and the row it says it in."""

    at: datetime
    statement: str
    snapshot_id: uuid.UUID
    engine: str


class OrderExplanationView(BaseModel):
    """Why an order is where it is (docs/ROADMAP_1.5.md AGT-4).

    Every fact cites a `decision_snapshots` row. `unexplained` is set instead
    when the record cannot answer - which is a different thing from there being
    no reason, and only one of them tells somebody what to fix.
    """

    order_id: uuid.UUID
    is_explained: bool
    facts: list[DecisionFactView]
    unexplained: str | None


class OverrideRequest(BaseModel):
    """A dispatcher overruling the queue on one order (`CON-2`).

    `reason_code` has no default and is not optional. That is the first of the
    three places CON-2's *"no override completes without a reason"* is enforced -
    the other two are a NOT NULL CHECK in migration `0060`, and the rule in
    `app/record/overrides.py` that `other` must carry a note. Three, because they
    stop three different things: a malformed request, a script that bypasses the
    API, and a reason that is technically present and says nothing.
    """

    action: str = Field(description="release or hold")
    reason_code: str = Field(description="One of app/models/dispatcher_override.py's codes")
    note: str | None = Field(
        default=None,
        max_length=2000,
        description="Required when reason_code is 'other'. Free text for a person to read.",
    )


class OverrideView(BaseModel):
    """One recorded override, and whether it was a disagreement.

    `contradicted_the_system` is not `action != system_action` computed by the
    caller: an override of a decision nobody recorded contradicts nothing, and a
    caller doing the subtraction itself would read the missing side as a mismatch
    and count it as a correction.
    """

    id: uuid.UUID
    order_id: uuid.UUID
    overridden_at: datetime
    action: str
    reason_code: str
    reason_label: str
    note: str | None
    by: str
    system_action: str | None
    system_reason: str | None
    system_decision_known: bool
    contradicted_the_system: bool
    order_status_before: str
    order_status_after: str


class OverrideReasonOption(BaseModel):
    """One choice in the reason list the console offers.

    Served rather than hardcoded in the dashboard, so the vocabulary the UI
    offers cannot drift from the one the database accepts - a drift whose
    symptom is a dispatcher picking a reason that is rejected at the moment they
    are least able to absorb it.
    """

    code: str
    label: str
    note_required: bool


class LateOrderView(BaseModel):
    """A late delivery nobody has judged yet (`REC-2`).

    The worklist that makes the consequence label possible. Without it the only
    way to record what happened after a late delivery is to already know which
    ones were late, which nobody does fourteen days later.
    """

    order_id: uuid.UUID
    external_ref: str
    client_id: uuid.UUID | None
    delivered_at: datetime | None
    minutes_late: int | None
    sla_tier: str | None


class ConsequenceRequest(BaseModel):
    """What actually happened after a late delivery (`REC-2`).

    `kind` is one of the six the brief names. There is no free-text-only path:
    a consequence nobody can count is not a label, and counting is the entire
    purpose - `DATA_NEED_BRIEF.md` §4.2 puts the requirement at 500-1,000
    observed consequences.
    """

    kind: str = Field(description="One of app/record/consequences.py's CONSEQUENCES")
    occurred_at: datetime | None = Field(
        default=None,
        description="When it happened. Defaults to now; set it when recording something from a few days ago.",
    )
    detail: str | None = Field(default=None, max_length=500)
    amount_cents: int | None = Field(
        default=None, description="For credit_issued - what it cost us."
    )


class ConsequenceOptionView(BaseModel):
    code: str
    label: str


class LinkageFlagView(BaseModel):
    """One question the linkage detectors raised (`REC-4`).

    A flag is a question, not a finding. The wording matters: a dispatcher who
    reads these as accusations stops reading them.
    """

    id: uuid.UUID
    kind: str
    # The ids this flag is about, shaped by kind - an order and a return, two
    # orders, or a dock and several orders. A dict rather than mostly-null
    # columns, matching the model for the reason its docstring gives.
    subjects: dict
    detail: str
    detected_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None


class WriterHealthView(BaseModel):
    """Whether one of the record's writers is producing anything (`REC-1`..`REC-4`).

    `last_written_at` rather than a boolean: "nothing this week" and "nothing
    since March" are both zero rows and mean entirely different things.
    """

    name: str
    rows_in_window: int
    last_written_at: datetime | None
    note: str


class RecordHealthView(BaseModel):
    """The record layer, reported on itself.

    Not a KPI and not the savings statement - those are claims about the
    business. This answers "is the record being written, and how far is the
    label set from being usable", which are questions about us.
    """

    window_days: int
    on_time_percentage: float | None
    on_time_numerator: int
    on_time_denominator: int
    on_time_interval: tuple[float, float] | None
    on_time_not_measured: str | None
    on_time_is_thin: bool
    decisions_recorded: int
    outcomes_recorded: int
    outcomes_linked_to_a_decision: int
    decision_link_percentage: float | None
    open_flags: int
    writers: list[WriterHealthView]
    # From `label_counts`: observed_consequences, silences, labelled_total,
    # by_type, and the band's two ends. Passed through rather than flattened,
    # because the band is a range the brief declines to collapse and a view that
    # picked a point target would lend it a precision the source does not have.
    labels: dict
