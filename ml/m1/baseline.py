"""The shrunk quantile baseline, and the metrics that judge it (`PRD-3`).

Promoted from `lmx-dwell/dwell/baseline.py`, which `ROADMAP_1.5.md` marks BUILT
with a one-word instruction: *"promote it. Beats a global model on held-out
data."* Rewritten on the standard library, otherwise faithful.

**Why a quantile and not a mean.** Two numbers do two jobs: p50 is what you plan
a route with, p90 is what you can promise a customer. A mean is neither. It sits
above the median of a right-skewed distribution and below anything you would
dare commit to, so it plans badly and promises worse.

**Why shrinkage.** A dock with four observed stops has a p90 that is mostly
noise; a dock with two hundred owns its own number. The weight `n / (n + k)`
moves smoothly between the two, and a dock with no history at all falls back
entirely to its node class - which is every dock at a new customer in week one.
`MODEL_AND_DATA_BRIEF.md` rule (5) calls this hierarchical pooling and says it
belongs in the schema from commit one because retrofitting it is painful.

`k = 10` is inherited from the analysis project. `DATA_NEED_BRIEF.md` §4.1
flags it as contested - an inherited "~30 per dock" figure sits in the same
documents with no derivation, and one of the two is wrong. Kept at 10 so the
promoted code is the code that produced the recorded result, with the
disagreement noted rather than quietly resolved.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ml.m1.features import DwellRow

PRIOR_STRENGTH = 10.0


def quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile. Matches the pandas default the analysis
    project used, so the promoted numbers are comparable to the recorded ones."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


@dataclass
class ShrunkQuantileBaseline:
    """Per-receiver quantile, shrunk toward the node class, then the book."""

    q: float = 0.5
    prior_strength: float = PRIOR_STRENGTH
    global_: float = 0.0
    class_: dict[str, float] = field(default_factory=dict)
    receiver_: dict[str, float] = field(default_factory=dict)
    receiver_n: dict[str, int] = field(default_factory=dict)
    receiver_class: dict[str, str] = field(default_factory=dict)

    name: str = "shrunk-quantile"

    def fit(self, rows: list[DwellRow]) -> ShrunkQuantileBaseline:
        self.global_ = quantile([r.dwell_min for r in rows], self.q)
        by_class: dict[str, list[float]] = {}
        by_receiver: dict[str, list[float]] = {}
        for row in rows:
            by_class.setdefault(row.node_class, []).append(row.dwell_min)
            by_receiver.setdefault(row.receiver_id, []).append(row.dwell_min)
            self.receiver_class[row.receiver_id] = row.node_class
        self.class_ = {k: quantile(v, self.q) for k, v in by_class.items()}
        self.receiver_ = {k: quantile(v, self.q) for k, v in by_receiver.items()}
        self.receiver_n = {k: len(v) for k, v in by_receiver.items()}
        return self

    def predict(self, rows: list[DwellRow]) -> list[float]:
        out = []
        for row in rows:
            # A receiver seen in training keeps the class it was seen as; an
            # unseen one falls back to the class on the row being predicted.
            node_class = self.receiver_class.get(row.receiver_id, row.node_class)
            prior = self.class_.get(node_class, self.global_)
            n = self.receiver_n.get(row.receiver_id, 0)
            own = self.receiver_.get(row.receiver_id, prior)
            weight = n / (n + self.prior_strength)
            out.append(weight * own + (1 - weight) * prior)
        return out


class GlobalQuantile:
    """One number for the whole book. The floor everything must clear.

    Worth having explicitly: if the shrunk baseline cannot beat a single
    constant, the per-dock structure is not carrying information and the honest
    move is to quote the constant.
    """

    name = "global-quantile"

    def __init__(self, q: float = 0.5):
        self.q = q
        self.value = 0.0

    def fit(self, rows: list[DwellRow]) -> GlobalQuantile:
        self.value = quantile([r.dwell_min for r in rows], self.q)
        return self

    def predict(self, rows: list[DwellRow]) -> list[float]:
        return [self.value] * len(rows)


def pinball_loss(actual: list[float], predicted: list[float], q: float) -> float:
    """The right metric for a quantile model. Lower is better.

    Asymmetric on purpose: at q=0.9 it charges nine times as much for being
    under as for being over, which is what makes it the loss for a promise
    rather than an estimate.
    """
    total = 0.0
    for y, p in zip(actual, predicted):
        d = y - p
        total += max(q * d, (q - 1) * d)
    return total / len(actual) if actual else 0.0


def coverage(actual: list[float], predicted: list[float]) -> float:
    """Share of stops that came in at or under the predicted value.

    For a p90 model this should land near 0.90 on held-out data. The number that
    makes this the load-bearing metric rather than a formality is in
    `MODEL_AND_DATA_BRIEF.md` rule (3): the p90 model covered **66.5%** of
    cold-start cases after promising 90%. A promise kept two times in three is
    not a p90; it is a p66 with a confident label.
    """
    if not actual:
        return 0.0
    return sum(1 for y, p in zip(actual, predicted) if y <= p) / len(actual)
