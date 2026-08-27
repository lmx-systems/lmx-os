"""
The shared vocabulary for reported numbers.

Extracted from `lmx_link.py` when the operations scorecard became a second consumer -
the same reason `app/sla/commitment.py` and `scripts/docx_house_style.py` exist. Two
copies of "how we report a metric" would have drifted, and the thing most likely to
drift is the part that matters: whether an absent number is allowed to look like a zero.

**Two shapes, because there are two kinds of question.**

`Measurement` is a distribution - "how long does this normally take". Reported as
median and p90 rather than a mean, because a mean is dominated by the one order
somebody left open over lunch and the question is what the normal case feels like.

`Rate` is a proportion - "how often does this hold". Reported with its numerator and
denominator visible, so 1/1 is never presented as 100% without the reader seeing that
it rests on a single event.

**Both can refuse to answer, and that is the load-bearing feature.** `not_measured`
carries a reason, and the two reasons are different in kind: "no data yet" resolves
itself once there is traffic, while "we don't record this" needs somebody to build
something. A metrics table that silently reports zero for both is worse than no table,
because the numbers get quoted.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Below this many observations, a percentage is arithmetic rather than information.
# Reported anyway - with the denominator attached - but flagged so a reader knows.
MIN_MEANINGFUL_SAMPLE = 20


@dataclass(frozen=True)
class Measurement:
    """One distribution, or an honest account of why there isn't one."""

    name: str
    target: str
    median: float | None = None
    p90: float | None = None
    sample_size: int = 0
    unit: str = "seconds"
    # Set instead of the numbers when the metric cannot be computed. The distinction
    # between "no data yet" and "we don't record this" matters: the first resolves
    # itself with traffic, the second needs somebody to build something.
    not_measured: str | None = None

    @property
    def meets_target(self) -> bool | None:
        return None


@dataclass(frozen=True)
class Rate:
    """One proportion, with the arithmetic left visible.

    `numerator` and `denominator` are part of the answer rather than working shown for
    politeness: "100% of deliveries hit their window" means something entirely different
    at n=1 and n=400, and a percentage alone cannot tell those apart.
    """

    name: str
    target: str
    numerator: int = 0
    denominator: int = 0
    not_measured: str | None = None

    @property
    def percentage(self) -> float | None:
        if self.not_measured is not None or not self.denominator:
            return None
        return round(100.0 * self.numerator / self.denominator, 1)

    @property
    def is_thin(self) -> bool:
        """Whether the denominator is too small for the percentage to mean much."""
        return 0 < self.denominator < MIN_MEANINGFUL_SAMPLE


def no_data(name: str, target: str, *, detail: str = "no completed records yet") -> Measurement:
    """The commonest honest answer before a pilot has run."""
    return Measurement(name=name, target=target, not_measured=detail)


async def percentiles(
    session: AsyncSession, expression, where
) -> tuple[float | None, float | None, int]:
    """(median, p90, n) for a numeric expression, or (None, None, 0) with no rows.

    `percentile_cont` rather than a Python-side sort: the rows never leave Postgres, so
    this stays one query whatever the volume becomes.
    """
    result = await session.execute(
        select(
            func.percentile_cont(0.5).within_group(expression.asc()),
            func.percentile_cont(0.9).within_group(expression.asc()),
            func.count(),
        ).where(where)
    )
    median, p90, count = result.one()
    if not count:
        return None, None, 0
    return (
        float(median) if median is not None else None,
        float(p90) if p90 is not None else None,
        int(count),
    )
