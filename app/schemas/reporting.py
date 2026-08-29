"""The LMX Link scorecard as it leaves the API (docs/LMX_LINK_PLAN.md §3.4)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
