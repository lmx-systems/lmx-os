"""The savings statement (`STL-1`).

Done when *"a customer can read it without a call."* That is a bar about the
prose as much as the arithmetic - a number somebody has to ring up to understand
is a number they will dispute - so every figure here carries its own basis in
the sentence that states it, not in a footnote.

## What this is, precisely: a sampled counterfactual

`EXP-1` dispatches 5-10% of a customer's orders the way they would have been
dispatched anyway. Comparing those against the rest is the only comparison in
this system that is caused rather than correlated, and it is the only thing a
savings claim may rest on. `ROADMAP_1.5.md` says the weaker version out loud:
`EXP-0`'s historical baseline *"is confounded by season, mix and volume... it is
not the counterfactual a savings statement rests on"*, and `DEC-0`'s shadow
divergence refuses a cost delta for a related reason - its half never happened.

## What this deliberately is not: a savings-share ledger

§2.2(d) is explicit that these are different objects:

> *"Phase 2 already carries EXP-1, STL-1 and STL-2, which together give a
> **sampled** counterfactual and a statement. A savings-share ledger is a
> different object: per-order, not sampled, and adversarial by design because
> the customer is arguing about their own invoice."*

A sampled estimate cannot be reconciled per order, and an invoice line built on
one would be indefensible the first time a customer asked which of their
deliveries it referred to. Matan's own argument on 10 September is the sharpest
statement of the problem: *"we performed so much better - and then [the
customer] is going to say, what are you talking about? It's us that performed so
much better."*

So this produces a statement and no billable amount, and `SavingsStatement`
carries no field a billing run could pick up. That is a structural refusal
rather than a convention: the commercial question is still open - savings share
was killed twice and reopened on 13 September - and code that quietly took a
side in it would settle by default what nobody has settled on purpose.

## The interval is the headline, not the midpoint

A difference between two means measured on a few hundred drops has real
uncertainty, and quoting the midpoint of it is how a statement becomes
indefensible. **If the interval includes zero, this says we cannot yet show a
saving** - not a smaller saving, not a saving "trending positive". That is the
honest reading, it is the one a customer's own analyst would reach, and hearing
it from us first is worth more than the quarter it delays the claim.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from statistics import NormalDist

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.experiment.exclusions import ExclusionImpact, exclusion_impact
from app.models.experiment_assignment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    EXPERIMENT_CONTROL_ARM,
    ExperimentAssignment,
)
from app.models.outcome_entry import KIND_COST, OutcomeEntry
from app.record.cost import RATE_PLACEHOLDER

# Below this many costed drops in an arm, no interval is produced at all.
# Cost per drop is right-skewed - one long route with a slow dock stretches the
# tail - so a normal approximation needs more than the textbook thirty before it
# means anything. A refusal here is a better statement than a wide number that
# reads as a small one.
MINIMUM_ARM_DROPS = 30

CONFIDENCE = 0.95


@dataclass(frozen=True)
class ArmSample:
    arm: str
    drops: int
    mean_cents: float
    stdev_cents: float

    @property
    def variance_of_mean(self) -> float:
        return (self.stdev_cents**2) / self.drops if self.drops else 0.0


@dataclass(frozen=True)
class ArmComparison:
    """Control against treatment, as an interval rather than a number."""

    control: ArmSample
    treatment: ArmSample
    difference_cents: float
    half_width_cents: float

    @property
    def low_cents(self) -> float:
        return self.difference_cents - self.half_width_cents

    @property
    def high_cents(self) -> float:
        return self.difference_cents + self.half_width_cents

    @property
    def spans_zero(self) -> bool:
        return self.low_cents <= 0 <= self.high_cents

    @property
    def shows_a_saving(self) -> bool:
        """Only when the whole interval is on the saving side of zero."""
        return self.low_cents > 0


@dataclass
class SavingsStatement:
    """Everything the statement says, and everything it declines to.

    Deliberately carries no billable amount. See the module docstring: a sampled
    estimate cannot be reconciled per order, and a field here called anything
    like `amount_due` would be picked up by a billing run eventually.
    """

    client_id: object
    period_start: datetime
    period_end: datetime
    drops: int
    costed_drops: int
    cost_per_drop_cents: float | None
    comparison: ArmComparison | None
    comparison_unavailable: str | None
    exclusions: ExclusionImpact | None = None
    caveats: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        """One sentence, and it is allowed to be the disappointing one."""
        if self.comparison is None:
            return "We cannot show a saving yet, and this explains why."
        if self.comparison.shows_a_saving:
            return (
                f"On the deliveries we measured, LMX cost "
                f"{_money(self.comparison.low_cents)} to "
                f"{_money(self.comparison.high_cents)} less per delivery."
            )
        return (
            "The measurement does not yet show a saving: the range still "
            "includes no difference at all."
        )


def _money(cents: float) -> str:
    """Currency a person reads, not a format string's idea of one.

    `f"${cents/100:.2f}"` prints `$-0.86` for a negative, which is the kind of
    detail that makes a customer trust the rest of the page less. The sign goes
    outside.
    """
    return f"-${abs(cents) / 100:.2f}" if cents < 0 else f"${cents / 100:.2f}"


def _sample(arm: str, values: list[float]) -> ArmSample:
    drops = len(values)
    if not drops:
        return ArmSample(arm=arm, drops=0, mean_cents=0.0, stdev_cents=0.0)
    mean = sum(values) / drops
    if drops < 2:
        return ArmSample(arm=arm, drops=drops, mean_cents=mean, stdev_cents=0.0)
    variance = sum((v - mean) ** 2 for v in values) / (drops - 1)
    return ArmSample(arm=arm, drops=drops, mean_cents=mean, stdev_cents=math.sqrt(variance))


def compare_arms(control: list[float], treatment: list[float]) -> ArmComparison | None:
    """Difference of means with a Welch interval, or nothing.

    Welch rather than a pooled interval because there is no reason to assume the
    two arms have the same spread - a control order dispatched the customer's
    old way is a different process, not the same process shifted.
    """
    if len(control) < MINIMUM_ARM_DROPS or len(treatment) < MINIMUM_ARM_DROPS:
        return None
    control_sample = _sample(ARM_CONTROL, control)
    treatment_sample = _sample(ARM_TREATMENT, treatment)
    standard_error = math.sqrt(
        control_sample.variance_of_mean + treatment_sample.variance_of_mean
    )
    z = NormalDist().inv_cdf(1 - (1 - CONFIDENCE) / 2)
    return ArmComparison(
        control=control_sample,
        treatment=treatment_sample,
        # Positive means treatment cost less, which is the direction a saving
        # runs. Stated this way round so the sign never has to be explained.
        difference_cents=control_sample.mean_cents - treatment_sample.mean_cents,
        half_width_cents=z * standard_error,
    )


async def _costs_by_order(
    session: AsyncSession, *, hub_id, period_start: datetime, period_end: datetime
) -> dict:
    """The live loaded cost per order from `REC-2`'s entries in the ledger.

    Superseded entries are dropped. A cost that was corrected should be read at
    its correction, and `REC-3` keeps both so the correction can be seen rather
    than so both can be counted.
    """
    entries = list(
        await session.scalars(
            select(OutcomeEntry).where(
                OutcomeEntry.hub_id == hub_id,
                OutcomeEntry.kind == KIND_COST,
                OutcomeEntry.occurred_at >= period_start,
                OutcomeEntry.occurred_at < period_end,
            )
        )
    )
    superseded = {e.supersedes for e in entries if e.supersedes is not None}
    live = [e for e in entries if e.id not in superseded]
    costs: dict = {}
    for entry in live:
        loaded = entry.values.get("loaded_cents")
        if loaded is not None:
            costs[entry.subject_id] = (loaded, entry.values.get("rate_source"))
    return costs


async def build_statement(
    session: AsyncSession,
    *,
    hub_id,
    client_id,
    period_start: datetime,
    period_end: datetime,
    order_counts_by_receiver: dict[str, int] | None = None,
) -> SavingsStatement:
    """Assemble what can be said about this period, and why the rest cannot."""
    costs = await _costs_by_order(
        session, hub_id=hub_id, period_start=period_start, period_end=period_end
    )
    assignments = list(
        await session.scalars(
            select(ExperimentAssignment).where(
                ExperimentAssignment.client_id == client_id,
                ExperimentAssignment.experiment == EXPERIMENT_CONTROL_ARM,
                ExperimentAssignment.assigned_at >= period_start,
                ExperimentAssignment.assigned_at < period_end,
            )
        )
    )

    statement = SavingsStatement(
        client_id=client_id,
        period_start=period_start,
        period_end=period_end,
        drops=len(assignments),
        costed_drops=0,
        cost_per_drop_cents=None,
        comparison=None,
        comparison_unavailable=None,
    )

    by_arm: dict[str, list[float]] = {ARM_CONTROL: [], ARM_TREATMENT: []}
    placeholder_rates = 0
    for assignment in assignments:
        found = costs.get(assignment.order_id)
        if found is None:
            continue
        loaded, rate_source = found
        by_arm.setdefault(assignment.arm, []).append(float(loaded))
        if rate_source == RATE_PLACEHOLDER:
            placeholder_rates += 1

    costed = by_arm[ARM_CONTROL] + by_arm[ARM_TREATMENT]
    statement.costed_drops = len(costed)
    if costed:
        statement.cost_per_drop_cents = sum(costed) / len(costed)

    if placeholder_rates:
        statement.caveats.append(
            f"{placeholder_rates} of {len(costed)} deliveries were costed against a "
            "placeholder wage rather than the driver's actual rate. Those figures "
            "are arithmetic on a number we made up and should not leave this page "
            "until the rate is recorded."
        )

    if not assignments:
        statement.comparison_unavailable = (
            "No orders were in the measured comparison during this period. The "
            "comparison works by dispatching a small share of orders the way they "
            "would have been dispatched before, and that has not been switched on "
            "for this account."
        )
    else:
        statement.comparison = compare_arms(by_arm[ARM_CONTROL], by_arm[ARM_TREATMENT])
        if statement.comparison is None:
            statement.comparison_unavailable = (
                f"Not enough deliveries yet: {len(by_arm[ARM_CONTROL])} in the "
                f"comparison group and {len(by_arm[ARM_TREATMENT])} outside it, "
                f"against a minimum of {MINIMUM_ARM_DROPS} each. A difference "
                "computed on fewer would be mostly noise, and we would rather say "
                "so than print it."
            )

    statement.exclusions = await exclusion_impact(
        session,
        client_id=client_id,
        order_counts_by_receiver=order_counts_by_receiver,
    )

    statement.caveats.append(
        "This is a sample, not an audit of every delivery. It cannot be broken "
        "down to say what any one order saved, and it is not the basis of an "
        "invoice."
    )
    statement.caveats.append(
        "Delivery cost here is driver time: the wage for the hours worked, shared "
        "across the deliveries made in them. It does not include fuel, the "
        "vehicle, or our own overhead."
    )
    return statement


def render_statement(statement: SavingsStatement) -> str:
    """Plain text a customer can read without a call.

    Short sentences, no jargon, and the uncomfortable parts in the body rather
    than at the bottom. The order is deliberate: what we did, what it cost, what
    the comparison shows or why it cannot, what it leaves out, and what it is
    not. A reader who stops halfway should not have stopped before a caveat that
    changes the number above it.
    """
    lines = [
        "Delivery savings statement",
        f"  {statement.period_start:%-d %B %Y} to {statement.period_end:%-d %B %Y}",
        "",
        statement.headline,
        "",
        "What we delivered",
        f"  {statement.drops} deliveries were included in the measurement.",
    ]
    if statement.cost_per_drop_cents is not None:
        lines.append(
            f"  We were able to cost {statement.costed_drops} of them, at an "
            f"average of {_money(statement.cost_per_drop_cents)} per delivery."
        )
    else:
        lines.append(
            "  None of them could be costed: costing needs the driver's shift "
            "hours, and those are not recorded for this period."
        )

    lines.extend(["", "The comparison"])
    if statement.comparison is not None:
        comparison = statement.comparison
        lines.append(
            f"  {comparison.control.drops} deliveries were dispatched the old way, "
            f"and {comparison.treatment.drops} the new way. They cost "
            f"{_money(comparison.control.mean_cents)} and "
            f"{_money(comparison.treatment.mean_cents)} each on average."
        )
        lines.append(
            f"  The difference is {_money(comparison.difference_cents)} per "
            f"delivery, and we are 95% confident the true figure is between "
            f"{_money(comparison.low_cents)} and "
            f"{_money(comparison.high_cents)}."
        )
        if comparison.spans_zero:
            lines.append(
                "  That range includes zero, which means this period does not yet "
                "show a saving. It does not mean there is none - only that we "
                "cannot demonstrate one yet, and we would rather tell you that "
                "than round it up."
            )
    else:
        lines.append(f"  {statement.comparison_unavailable}")

    if statement.exclusions is not None:
        lines.extend(["", "What this does not cover", f"  {statement.exclusions.disclosure()}"])

    lines.extend(["", "How to read this"])
    for caveat in statement.caveats:
        lines.append(f"  {caveat}")
    return "\n".join(lines)
