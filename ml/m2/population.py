"""Who we deliver to, and how much it costs them when we are late.

This is the planted ground truth for `M2`. The point of generating data rather
than waiting for it is that the answer is known in advance: if the harness
cannot recover a function we wrote down, it will not recover one we did not.

**The shape of the truth.** `M2` predicts *P(a consequence occurs | this order
is late)*. Here that is a logistic function of things a dispatcher could
plausibly know at decision time - node class, how late, order value, hour of
day - plus a per-location effect drawn from a prior attached to the node class.
The hierarchy is not decoration. `MODEL_AND_DATA_BRIEF.md` rule (5) says cold
start belongs in the schema from commit one, and a corpus where every location
is independent cannot exercise the one thing cold start means: a dock nobody
has seen inheriting its class's prior.

**The intercept is solved, not chosen.** Field evidence in the brief says one
late stop in six has a credible urgency argument, at a customer who states that
all of his deliveries are urgent. So the baseline consequence rate is fitted by
bisection until the population mean lands on that figure, and the `[ASSUMPTION]`
is then a single number in one place rather than a constant smeared across a
dozen coefficients where nobody can find it to challenge it.

**What this is not.** It is not evidence about urgency. Every coefficient below
is a guess with a reason attached, and a model trained here has learned our
guesses. The corpus exists to test the machinery - leakage, splits,
calibration, the arm - against a known answer. Any number produced from it that
escapes into a deck is a fabrication. See `ml/m2/corpus.py`, which stamps every
row it writes for exactly that reason.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from app.identity.node_class import (
    NODE_CLASS_BODY_SHOP,
    NODE_CLASS_DEALER,
    NODE_CLASS_MUNICIPAL,
    NODE_CLASS_PARTS_STORE,
    NODE_CLASS_SHOP,
    NODE_CLASS_TRANSFER,
    NODE_CLASS_WAREHOUSE,
)

# `docs/DATA_NEED_BRIEF.md` §4.2, measured on one full manifest and the only
# non-assumption in the chain: roughly one late stop in six has a credible
# urgency argument. Everything else here is shaped to hit this.
TARGET_CONSEQUENCE_RATE_WHEN_LATE = 1.0 / 6.0

# How much being late matters, by what kind of place is waiting. Log-odds
# offsets against the population baseline. Each has a reason, because a
# coefficient without one cannot be argued with:
#
#   shop          an independent repairer with a car on a lift and no second
#                 bay. The most exposed node on the network.
#   dealer        a service department, also waiting on a lift, but with a
#                 parts counter of its own and somewhere to put the job.
#   body_shop     scheduled work, insurer timelines, real slack in the day.
#   municipal     a fleet yard. Slow to shout, but it will document the miss
#                 and take the credit, because procurement requires it to.
#   parts_store   holds stock and can substitute. Late is an annoyance.
#   warehouse     an internal network node. Nobody is waiting at the counter.
#   transfer      the same, more so: it is a leg of our own movement.
#
# `MODEL_AND_DATA_BRIEF.md` §M3 records that hold-window value is node-class
# specific "to an extreme degree" - +3-7% at shops against +195-271% at
# warehouse and transfer. The spread below runs the same direction, which is
# the point: the nodes where holding buys the most are the nodes where being
# late costs the least, and a model that cannot see that will hold the wrong
# orders.
NODE_CLASS_URGENCY: dict[str, float] = {
    NODE_CLASS_SHOP: 1.10,
    NODE_CLASS_DEALER: 0.70,
    NODE_CLASS_BODY_SHOP: 0.15,
    NODE_CLASS_MUNICIPAL: -0.10,
    NODE_CLASS_PARTS_STORE: -0.60,
    NODE_CLASS_WAREHOUSE: -1.30,
    NODE_CLASS_TRANSFER: -1.60,
}

# How much of a hub's book each class is. Skewed on purpose: `docs/DATA_NEED_
# BRIEF.md` §4.4 note 3 warns that node-class frequency is skewed and the
# binding sample size is the rarest class, and a corpus with seven equal classes
# would hide that problem instead of posing it.
NODE_CLASS_SHARE: dict[str, float] = {
    NODE_CLASS_SHOP: 0.34,
    NODE_CLASS_PARTS_STORE: 0.21,
    NODE_CLASS_BODY_SHOP: 0.17,
    NODE_CLASS_DEALER: 0.13,
    NODE_CLASS_WAREHOUSE: 0.08,
    NODE_CLASS_MUNICIPAL: 0.05,
    NODE_CLASS_TRANSFER: 0.02,
}

# Spread of the per-location effect within a class, in log-odds. Two shops are
# not the same shop: one runs a tight schedule and rings up at ten minutes,
# another is relaxed. Wide enough that the location term carries real signal -
# so a leaky feature built from it pays off visibly, which is what makes the
# leakage test in the harness a test rather than a formality.
LOCATION_EFFECT_SIGMA = 0.55

# Being five minutes late is not a tenth of being fifty minutes late. The dose
# response saturates: past an hour or so the damage is done and further delay
# adds little, because the customer has already made whatever call they were
# going to make.
LATENESS_HALF_LIFE_MINUTES = 45.0
LATENESS_WEIGHT = 1.40

# A $2,000 part on a dead vehicle is a different event from a $12 clip. Applied
# to log10(value) so it is a shape rather than a straight line.
ORDER_VALUE_WEIGHT = 0.45
REFERENCE_ORDER_VALUE = 74.0  # median invoice in the design partner's export

# Late in the working day is worse: a miss at 16:30 is a vehicle that does not
# leave tonight, and there is no recovery before tomorrow.
END_OF_DAY_WEIGHT = 0.80


@dataclass(frozen=True)
class Location:
    """A dock, with its class and its own temperament."""

    location_id: str
    node_class: str
    # Log-odds offset for this specific dock, drawn from its class's prior.
    # Unknown to any model at the moment a new dock first appears - which is
    # the whole of the cold-start problem, stated as a field.
    effect: float


@dataclass(frozen=True)
class Customer:
    """An account. Orders inherit its SLA tier and its dock mix."""

    customer_id: str
    sla_tier: str
    locations: tuple[Location, ...]


def lateness_response(minutes_late: float) -> float:
    """Saturating dose response, 0 at not-late and approaching 1."""
    if minutes_late <= 0:
        return 0.0
    return 1.0 - math.exp(-minutes_late / LATENESS_HALF_LIFE_MINUTES)


def urgency_logit(
    *,
    baseline: float,
    location: Location,
    minutes_late: float,
    order_value: float,
    hour_of_day: int,
) -> float:
    """The planted truth, in log-odds.

    Everything here is knowable at decision time except `minutes_late`, which
    is the conditioning event rather than a feature: `M2` answers "if this runs
    late, what then", so the model is asked to integrate over plausible
    lateness. The harness holds it fixed at a stated horizon rather than
    reading the realised value, which would be the future leaking into the
    question.
    """
    value_term = ORDER_VALUE_WEIGHT * math.log10(
        max(order_value, 1.0) / REFERENCE_ORDER_VALUE
    )
    # Ramps in over the afternoon rather than switching on at a threshold.
    end_of_day = END_OF_DAY_WEIGHT * max(0.0, (hour_of_day - 12) / 6.0)
    return (
        baseline
        + NODE_CLASS_URGENCY[location.node_class]
        + location.effect
        + LATENESS_WEIGHT * lateness_response(minutes_late)
        + value_term
        + end_of_day
    )


def probability_of_consequence(
    *,
    baseline: float,
    location: Location,
    minutes_late: float,
    order_value: float,
    hour_of_day: int,
) -> float:
    """`P(consequence | late)` under the planted truth."""
    if minutes_late <= 0:
        return 0.0
    logit = urgency_logit(
        baseline=baseline,
        location=location,
        minutes_late=minutes_late,
        order_value=order_value,
        hour_of_day=hour_of_day,
    )
    return 1.0 / (1.0 + math.exp(-logit))


def build_locations(rng: random.Random, count: int) -> tuple[Location, ...]:
    """A book of docks, class-weighted, each with its own effect."""
    classes = list(NODE_CLASS_SHARE)
    weights = [NODE_CLASS_SHARE[c] for c in classes]
    locations = []
    for index in range(count):
        node_class = rng.choices(classes, weights=weights, k=1)[0]
        locations.append(
            Location(
                location_id=f"LOC-{index:05d}",
                node_class=node_class,
                effect=rng.gauss(0.0, LOCATION_EFFECT_SIGMA),
            )
        )
    return tuple(locations)


def solve_baseline(
    locations: tuple[Location, ...],
    *,
    sample_minutes_late: float,
    sample_order_value: float,
    sample_hour: int,
    target: float = TARGET_CONSEQUENCE_RATE_WHEN_LATE,
) -> float:
    """Find the intercept that puts the mean consequence rate on the target.

    Bisection rather than algebra because the mean of a logistic is not the
    logistic of the mean, and pretending otherwise would miss the target by
    several points in exactly the direction that flatters us.
    """
    def mean_rate(baseline: float) -> float:
        total = sum(
            probability_of_consequence(
                baseline=baseline,
                location=loc,
                minutes_late=sample_minutes_late,
                order_value=sample_order_value,
                hour_of_day=sample_hour,
            )
            for loc in locations
        )
        return total / len(locations)

    low, high = -12.0, 12.0
    for _ in range(80):
        mid = (low + high) / 2.0
        if mean_rate(mid) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0
