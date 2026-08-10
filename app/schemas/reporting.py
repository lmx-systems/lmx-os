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
