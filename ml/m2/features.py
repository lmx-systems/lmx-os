"""Features that only know the past (`MODEL_AND_DATA_BRIEF.md` rule 1).

> *"Computing a location's average over the whole file leaks the answer into the
> question. You get a beautiful validation score and a model that collapses in
> production. This is the most common quiet failure in logistics ML and we treat
> it as a build-breaking bug."*

The word doing the work there is **quiet**. Leakage does not raise. It does not
fail a type check. It makes every number better, which is why it survives
review: the person who introduces it sees the validation score improve and
concludes they have done well. The only defence is that the correct version and
the leaky version both exist, and something compares them - which is
`gates.py`, and why `leaky_location_rate` below is written on purpose rather
than avoided.

**Two histories, one shrunk into the other.** A location's own consequence rate
is the sharpest feature available and it is worthless on a dock we have never
seen - which is every dock in a new customer's first week, the exact moment a
prospect is watching. So the location rate is shrunk toward its node class's
rate with a fixed prior strength, and a location with no history falls back to
its class entirely. That is rule (5): hierarchical pooling in the schema from
commit one, because retrofitting it is painful and it decides whether "it gets
better with scale" is true.

**Only late deliveries carry a label.** `M2` is *P(consequence | late)*, so an
on-time delivery is not a negative example - it is not an example. It still
counts toward how much we know about a dock, which is why the exposure counts
below run over all deliveries and the rates run over late ones.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.record.consequences import SILENCE
from ml.m2.corpus import Delivery

# `docs/DATA_NEED_BRIEF.md` §4.1 records the repo's shrinkage baseline at
# `prior_strength = 10.0`, against an inherited "~30 per dock" figure with no
# derivation anywhere. The brief flags the two as contradictory and unresolved.
# Using 10.0 keeps this consistent with the baseline we have to beat; the open
# question is noted rather than quietly settled here.
PRIOR_STRENGTH = 10.0


@dataclass(frozen=True)
class FeatureRow:
    """One training row: what was knowable, and what happened."""

    order_id: str
    delivered_on: str
    location_id: str
    node_class: str
    # Knowable at decision time.
    log_order_value: float
    hour_of_day: int
    day_of_week: int
    location_deliveries_so_far: int
    location_late_so_far: int
    shrunk_location_rate: float
    node_class_rate: float
    # The label: 1 if a consequence was observed, 0 if silence was recorded.
    label: int
    # Carried through for splitting and for scoring against planted truth.
    arm: str
    true_probability: float


def _label(delivery: Delivery) -> int | None:
    """1 for an observed consequence, 0 for recorded silence, None for no label.

    Silence is a zero, not a missing value. `app/record/consequences.py` records
    it explicitly for exactly this reason: an absent row is ambiguous between
    "nothing happened" and "nobody looked", and treating the second as the first
    trains the model that lateness is usually free.
    """
    if not delivery.was_late or delivery.observed_label is None:
        return None
    return 0 if delivery.observed_label == SILENCE else 1


def build_features(deliveries: list[Delivery]) -> list[FeatureRow]:
    """Walk the book in time order, computing every history from the past only.

    One pass, updating the accumulators *after* each row is emitted. That
    ordering is the whole of rule (1): a row never sees its own outcome, and
    nothing is computed from a dataframe-wide aggregate that would include it.
    """
    ordered = sorted(deliveries, key=lambda d: (d.delivered_on, d.order_id))

    loc_deliveries: dict[str, int] = {}
    loc_late: dict[str, int] = {}
    loc_consequences: dict[str, int] = {}
    class_late: dict[str, int] = {}
    class_consequences: dict[str, int] = {}

    rows: list[FeatureRow] = []
    for delivery in ordered:
        loc = delivery.location_id
        node_class = delivery.node_class

        class_n = class_late.get(node_class, 0)
        class_rate = (
            class_consequences.get(node_class, 0) / class_n if class_n else 0.0
        )
        own_n = loc_late.get(loc, 0)
        own_rate = loc_consequences.get(loc, 0) / own_n if own_n else 0.0
        # Shrinkage toward the class. With no history the weight is zero and the
        # answer is the class rate, which is precisely what a cold dock gets.
        weight = own_n / (own_n + PRIOR_STRENGTH)
        shrunk = weight * own_rate + (1 - weight) * class_rate

        label = _label(delivery)
        if label is not None:
            year, month, day = (int(p) for p in delivery.delivered_on.split("-"))
            rows.append(
                FeatureRow(
                    order_id=delivery.order_id,
                    delivered_on=delivery.delivered_on,
                    location_id=loc,
                    node_class=node_class,
                    log_order_value=_log10(delivery.order_value),
                    hour_of_day=delivery.hour_of_day,
                    day_of_week=_weekday(year, month, day),
                    location_deliveries_so_far=loc_deliveries.get(loc, 0),
                    location_late_so_far=own_n,
                    shrunk_location_rate=shrunk,
                    node_class_rate=class_rate,
                    label=label,
                    arm=delivery.arm,
                    true_probability=delivery.true_probability_realised,
                )
            )

        # Accumulate only after emitting. Moving these three lines above the
        # append is the entire bug this module exists to prevent.
        loc_deliveries[loc] = loc_deliveries.get(loc, 0) + 1
        if delivery.was_late:
            loc_late[loc] = own_n + 1
            class_late[node_class] = class_n + 1
            if label == 1:
                loc_consequences[loc] = loc_consequences.get(loc, 0) + 1
                class_consequences[node_class] = class_consequences.get(node_class, 0) + 1

    return rows


def leaky_location_rate(deliveries: list[Delivery]) -> dict[str, float]:
    """The forbidden feature, written down so it can be measured.

    Each location's consequence rate computed over the **whole** file, including
    the row it will be attached to. This is the mistake rule (1) names, and it
    is here because a rule nobody can demonstrate breaking is a rule nobody
    believes. `gates.py` fits with and without it and shows the leak scoring
    better on validation and worse on the future - which is the shape of the
    failure, and the reason a good validation number proves nothing on its own.

    Never call this from a training path.
    """
    late: dict[str, int] = {}
    hits: dict[str, int] = {}
    for delivery in deliveries:
        label = _label(delivery)
        if label is None:
            continue
        late[delivery.location_id] = late.get(delivery.location_id, 0) + 1
        hits[delivery.location_id] = hits.get(delivery.location_id, 0) + label
    return {loc: hits[loc] / late[loc] for loc in late}


def _log10(value: float) -> float:
    from math import log10

    return log10(max(value, 1.0))


def _weekday(year: int, month: int, day: int) -> int:
    from datetime import date

    return date(year, month, day).weekday()
