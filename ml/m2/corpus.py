"""A corpus of deliveries at `M2` scale, with the answer planted in it.

`docs/DATA_NEED_BRIEF.md` §4.2 sizes `M2` at 500-1,000 observed consequences,
which at its assumed rate of 0.008 per delivery is 62,500 to 125,000
deliveries. This generates them, along with the two things a real book of that
size would also contain and which are the actual reason to bother:

**A policy confound.** Under normal dispatch, whether an order runs late is
decided by our own batching policy - and that policy rationally holds the nodes
where holding pays, which `MODEL_AND_DATA_BRIEF.md` §M3 measures as warehouse
and transfer (+195-271%) rather than shops (+3-7%). Those are the same nodes
where being late costs the customer least. So the late population is enriched
with exactly the deliveries nobody minds, a model fit on it concludes lateness
is usually free, and that conclusion licenses holding more. The loop closes:
the model justifies the policy that produced its training data. This is planted
deliberately, at a known size, so the harness can be shown to detect it.

**A control arm.** `EXP-1` orders are dispatched as the customer would have, so
lateness there is not produced by our policy. Arm membership is random, so the
two populations are comparable and the gap between them is the bias, measured.

**Observation is censored, and not at random.** The brief assumes about half of
consequences are observable. That average hides the part that matters: an
escalation call and a credit are in our own systems, while a part quietly
sourced from a competitor is invisible unless somebody asks. The invisible ones
are the commercially serious ones, so a model trained on observed labels is
blind in the direction that costs the most. Modelled per type rather than as
one coin flip, because the difference is the finding.

---

**A corpus is not evidence.** Every coefficient in `population.py` is a guess
with a reason attached. A model trained here has learned our guesses, and a
score computed here measures whether the harness can recover a function we
wrote down - nothing about urgency, nothing about the business. Every row
carries `synthetic=true` and every summary carries the provenance banner, so a
number lifted out of this and put in front of an investor has to have had the
warning removed by hand.
"""
from __future__ import annotations

import csv
import math
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from app.record.consequences import (
    CONSEQUENCE_CANCELLED,
    CONSEQUENCE_COMPETITOR,
    CONSEQUENCE_CREDIT,
    CONSEQUENCE_ESCALATION,
    CONSEQUENCE_REORDER_GAP,
    CONSEQUENCE_RETURNED,
    SILENCE,
)
from ml.m2.population import (
    NODE_CLASS_SHARE,
    Location,
    build_locations,
    probability_of_consequence,
    solve_baseline,
)

ARM_CONTROL = "control"
ARM_TREATMENT = "treatment"

# `docs/ROADMAP_1.5.md` EXP-1 and the band `app/experiment/arms.py` enforces.
# The midpoint, so a corpus is not quietly generated at the most flattering end.
DEFAULT_ARM_FRACTION = 0.08

# `[ASSUMPTION]` `docs/DATA_NEED_BRIEF.md` §4.2: about one delivery in ten runs
# late. Distributed across node classes by the policy below rather than applied
# flat, but weighted to land on this overall.
TARGET_LATE_RATE = 0.10

# How often each class runs late under our own dispatch. The ordering is the
# confound: the classes we hold longest are the classes that mind least. These
# are shaped to average to TARGET_LATE_RATE against NODE_CLASS_SHARE - asserted
# in the tests rather than trusted, because a drift here silently changes what
# the corpus is a corpus of.
POLICY_LATE_RATE: dict[str, float] = {
    "transfer": 0.26,
    "warehouse": 0.22,
    "municipal": 0.13,
    "parts_store": 0.12,
    "body_shop": 0.10,
    "dealer": 0.07,
    "shop": 0.05,
}

# In the control arm the order goes out as the customer would have sent it, so
# our batching is not what makes it late. Flat across classes: whatever makes a
# control order late - traffic, a long leg, the customer's own timing - is not
# reading our node-class table.
CONTROL_LATE_RATE = 0.10

# How late, when late. Lognormal: most misses are small, a thin tail is hours.
_LATE_LOG_MEAN = math.log(22.0)
_LATE_LOG_SIGMA = 1.05

# `[ASSUMPTION]` Which consequence, given one occurred. Silent defection is the
# most common and the least visible, which is the uncomfortable pairing.
CONSEQUENCE_MIX: dict[str, float] = {
    CONSEQUENCE_ESCALATION: 0.20,
    CONSEQUENCE_CREDIT: 0.12,
    CONSEQUENCE_RETURNED: 0.13,
    CONSEQUENCE_CANCELLED: 0.08,
    CONSEQUENCE_COMPETITOR: 0.28,
    CONSEQUENCE_REORDER_GAP: 0.19,
}

# `[ASSUMPTION]` The probability we ever find out, by type. The brief's "about
# half are observable" is the weighted average of these, and the average is
# what hides the problem:
#
#   escalation_call     they rang us. It is in the call log.
#   credit_issued       it is in billing.
#   order_cancelled     it is in the order state machine.
#   part_returned       a return exists, but tying it to lateness needs a human
#                       who remembers why.
#   competitor_sourced  invisible unless somebody asks. `REC-2`'s one SMS is
#                       the only thing that moves this number.
#   reorder_gap         only visible as a trend, months later, if at all.
OBSERVABILITY: dict[str, float] = {
    CONSEQUENCE_ESCALATION: 0.95,
    CONSEQUENCE_CREDIT: 0.90,
    CONSEQUENCE_CANCELLED: 0.85,
    CONSEQUENCE_RETURNED: 0.60,
    CONSEQUENCE_COMPETITOR: 0.15,
    CONSEQUENCE_REORDER_GAP: 0.10,
}

# Order value. Lognormal around the design partner's median invoice of $74.
_VALUE_LOG_MEAN = math.log(74.0)
_VALUE_LOG_SIGMA = 1.15

# The horizon `M2` is asked about: "if this ran this late, what then". Fixed so
# the question is the same for every order. Reading each order's realised
# lateness into its own feature row would be the future leaking into the
# question - the model would be told the answer and score beautifully.
EVALUATION_HORIZON_MINUTES = 30.0

_OPERATING_DAYS_PER_WEEK = 5

# What a model may see, and what it may never see. Stated here rather than left
# to whoever writes the training script, because the difference between these
# two tuples is the difference between a harness and a leak - and the leak
# scores better, so nothing about a good validation number will reveal it.
FEATURE_COLUMNS: tuple[str, ...] = (
    "delivered_on",
    "hour_of_day",
    "location_id",
    "node_class",
    "order_value",
)

# The planted answer, plus the two fields that describe the realised outcome.
# `was_late` and `minutes_late` are in here not because they are secret but
# because `M2` is asked before the delivery runs: "if this is late, what then".
# A model given the realised lateness has been told half the answer.
TRUTH_COLUMNS: tuple[str, ...] = (
    "was_late",
    "minutes_late",
    "true_consequence",
    "observed_label",
    "true_probability_at_horizon",
    "true_probability_realised",
)

# The label a model is trained against - derived from `observed_label`, which
# is censored, and never from `true_consequence`, which no real book contains.
LABEL_COLUMN = "observed_label"


@dataclass(frozen=True)
class Delivery:
    """One delivery, with both what happened and what was true.

    The `true_*` fields are not features. They are the planted answer, kept in
    the same row so a harness can be scored against it, and excluded by name
    from anything a model is allowed to see.
    """

    order_id: str
    delivered_on: str
    hour_of_day: int
    location_id: str
    node_class: str
    order_value: float
    arm: str
    was_late: bool
    minutes_late: float
    # What actually happened, including the consequences nobody saw.
    true_consequence: str | None
    # What the label set would contain: the consequence if it was observable,
    # `silence` if the order was late and nothing was seen, None if on time.
    observed_label: str | None
    # The planted P(consequence | late) at the fixed horizon. The target.
    true_probability_at_horizon: float
    # Same, at the lateness this order actually ran. Useful for scoring the
    # realised draw; never a feature.
    true_probability_realised: float
    synthetic: bool = True


@dataclass
class Corpus:
    deliveries: list[Delivery]
    locations: tuple[Location, ...]
    baseline_logit: float
    seed: int
    arm_fraction: float

    def summary(self) -> dict:
        """The numbers worth checking before anybody trains on this."""
        total = len(self.deliveries)
        late = [d for d in self.deliveries if d.was_late]
        control = [d for d in self.deliveries if d.arm == ARM_CONTROL]
        control_late = [d for d in control if d.was_late]
        treatment_late = [d for d in late if d.arm == ARM_TREATMENT]
        true_conseq = [d for d in self.deliveries if d.true_consequence]
        observed = [
            d for d in self.deliveries
            if d.observed_label is not None and d.observed_label != SILENCE
        ]
        observed_control = [d for d in observed if d.arm == ARM_CONTROL]

        def rate(subset: list[Delivery]) -> float:
            if not subset:
                return 0.0
            return sum(1 for d in subset if d.true_consequence) / len(subset)

        return {
            "synthetic": True,
            "seed": self.seed,
            "deliveries": total,
            "locations": len(self.locations),
            "late": len(late),
            "late_rate": len(late) / total if total else 0.0,
            "arm_fraction_realised": len(control) / total if total else 0.0,
            "true_consequences": len(true_conseq),
            "observed_consequences": len(observed),
            "observed_consequences_in_control_arm": len(observed_control),
            "observation_rate": (
                len(observed) / len(true_conseq) if true_conseq else 0.0
            ),
            # The confound, measured. These two estimate the same quantity and
            # disagree; the gap is what EXP-1 buys.
            "consequence_rate_when_late_observational": rate(treatment_late),
            "consequence_rate_when_late_in_arm": rate(control_late),
            "confound_bias": rate(treatment_late) - rate(control_late),
        }

    def write_csv(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [asdict(d) for d in self.deliveries]
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path


def _weekday_sequence(start: date, days: int) -> list[date]:
    """Operating days. `docs/DATA_NEED_BRIEF.md` §4.4 counts in these, not
    calendar days, and a corpus dated across weekends would overstate how fast
    labels arrive by two sevenths."""
    out: list[date] = []
    cursor = start
    while len(out) < days:
        if cursor.weekday() < _OPERATING_DAYS_PER_WEEK:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _consequence_type(rng: random.Random) -> str:
    kinds = list(CONSEQUENCE_MIX)
    return rng.choices(kinds, weights=[CONSEQUENCE_MIX[k] for k in kinds], k=1)[0]


def generate_corpus(
    *,
    deliveries: int = 78_000,
    locations: int = 240,
    operating_days: int = 380,
    arm_fraction: float = DEFAULT_ARM_FRACTION,
    seed: int = 20260916,
    start: date = date(2026, 1, 5),
) -> Corpus:
    """Generate the corpus. Same seed, same corpus.

    `locations` defaults near the design partner's 229 receivers so the
    deliveries-per-dock density is in the right region; `operating_days` is
    about eighteen months of five-day weeks, long enough that a chronological
    split has a real future side to hold out.
    """
    rng = random.Random(seed)
    book = build_locations(rng, locations)
    baseline = solve_baseline(
        book,
        sample_minutes_late=EVALUATION_HORIZON_MINUTES,
        sample_order_value=74.0,
        sample_hour=13,
    )
    days = _weekday_sequence(start, operating_days)

    rows: list[Delivery] = []
    for index in range(deliveries):
        day = days[index % len(days)]
        location = book[rng.randrange(len(book))]
        hour = rng.choices(
            [8, 9, 10, 11, 12, 13, 14, 15, 16, 17],
            weights=[6, 11, 13, 12, 8, 11, 12, 11, 10, 6],
            k=1,
        )[0]
        value = rng.lognormvariate(_VALUE_LOG_MEAN, _VALUE_LOG_SIGMA)
        arm = ARM_CONTROL if rng.random() < arm_fraction else ARM_TREATMENT

        late_rate = (
            CONTROL_LATE_RATE if arm == ARM_CONTROL
            else POLICY_LATE_RATE[location.node_class]
        )
        was_late = rng.random() < late_rate
        minutes_late = (
            rng.lognormvariate(_LATE_LOG_MEAN, _LATE_LOG_SIGMA) if was_late else 0.0
        )

        realised_p = probability_of_consequence(
            baseline=baseline,
            location=location,
            minutes_late=minutes_late,
            order_value=value,
            hour_of_day=hour,
        )
        horizon_p = probability_of_consequence(
            baseline=baseline,
            location=location,
            minutes_late=EVALUATION_HORIZON_MINUTES,
            order_value=value,
            hour_of_day=hour,
        )

        true_consequence: str | None = None
        observed_label: str | None = None
        if was_late:
            if rng.random() < realised_p:
                true_consequence = _consequence_type(rng)
                seen = rng.random() < OBSERVABILITY[true_consequence]
                # A consequence we never saw is indistinguishable from nothing
                # having happened - which is why silence has to be recorded
                # rather than inferred, and why this row says `silence` rather
                # than leaving the label empty.
                observed_label = true_consequence if seen else SILENCE
            else:
                observed_label = SILENCE

        rows.append(
            Delivery(
                order_id=f"SYN-{seed}-{index:07d}",
                delivered_on=day.isoformat(),
                hour_of_day=hour,
                location_id=location.location_id,
                node_class=location.node_class,
                order_value=round(value, 2),
                arm=arm,
                was_late=was_late,
                minutes_late=round(minutes_late, 2),
                true_consequence=true_consequence,
                observed_label=observed_label,
                true_probability_at_horizon=round(horizon_p, 6),
                true_probability_realised=round(realised_p, 6),
            )
        )

    return Corpus(
        deliveries=rows,
        locations=book,
        baseline_logit=baseline,
        seed=seed,
        arm_fraction=arm_fraction,
    )


def expected_late_rate() -> float:
    """What `POLICY_LATE_RATE` averages to across the book. Asserted in tests."""
    return sum(NODE_CLASS_SHARE[c] * POLICY_LATE_RATE[c] for c in NODE_CLASS_SHARE)


def expected_observation_rate() -> float:
    """What `OBSERVABILITY` averages to across `CONSEQUENCE_MIX`."""
    return sum(CONSEQUENCE_MIX[k] * OBSERVABILITY[k] for k in CONSEQUENCE_MIX)
