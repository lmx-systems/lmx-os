"""Calibration, because the output is traded against cost (rule 4).

> *"The output is traded against cost in the solver. We need a probability, not
> a class."*

That sentence decides everything in this file. A classifier that ranks urgent
orders above calm ones but quotes 0.4 where the truth is 0.15 will rank a
route correctly and price it wrong, and the solver's whole job is the pricing.
Discrimination and calibration are different properties and a model can have
one without the other, so both are measured here and neither is allowed to
stand in for the other.

**Isotonic, per the brief, and hand-rolled.** Pool-adjacent-violators is forty
lines and exact. Taking `scikit-learn` as a hard dependency of the metric layer
would mean the calibration gate cannot run in CI unless the ML stack is
installed, and a gate that only runs sometimes is not a gate. The estimator may
need the library; the check on the estimator must not.

**The censoring correction, and why it is stated rather than applied.** About
half of consequences are ever observed (`docs/DATA_NEED_BRIEF.md` §4.2). A
perfectly calibrated model fit on censored labels is calibrated *to the labels*
and therefore quotes roughly half the true probability - not noisily, but
systematically, in the direction that makes urgency look cheap. The correction
is one division. The reason it is reported and not silently applied is that the
divisor is an assumption: nobody has measured the real observation rate, and
`REC-2`'s one SMS after a late delivery is the instrument that would. Applying
an unmeasured constant inside a calibration step is how a guess becomes a
number nobody remembers is a guess.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def isotonic_fit(
    scores: list[float], labels: list[int]
) -> tuple[list[float], list[float]]:
    """Pool adjacent violators. Returns (breakpoints, fitted values).

    Sorts by score and merges any adjacent pair whose fitted values run
    downhill, weighting by block size, until the sequence is non-decreasing.
    Exact, deterministic, and O(n) after the sort.
    """
    if not scores:
        return [], []
    pairs = sorted(zip(scores, labels))
    # Each block: [sum of labels, count, rightmost score].
    blocks: list[list[float]] = []
    for score, label in pairs:
        blocks.append([float(label), 1.0, score])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            merged_sum = blocks[-2][0] + blocks[-1][0]
            merged_n = blocks[-2][1] + blocks[-1][1]
            right = blocks[-1][2]
            blocks.pop()
            blocks[-1] = [merged_sum, merged_n, right]
    return [b[2] for b in blocks], [b[0] / b[1] for b in blocks]


@dataclass
class IsotonicCalibrator:
    """Maps a raw score onto a probability, monotonically."""

    breakpoints: list[float]
    values: list[float]

    @classmethod
    def fit(cls, scores: list[float], labels: list[int]) -> IsotonicCalibrator:
        breakpoints, values = isotonic_fit(scores, labels)
        return cls(breakpoints=breakpoints, values=values)

    def transform(self, score: float) -> float:
        """Step lookup. Below the first block the first value applies."""
        if not self.breakpoints:
            return 0.0
        low, high = 0, len(self.breakpoints) - 1
        while low < high:
            mid = (low + high) // 2
            if self.breakpoints[mid] < score:
                low = mid + 1
            else:
                high = mid
        return self.values[low]


def brier_score(predictions: list[float], labels: list[int]) -> float:
    """Mean squared error against the outcome. Lower is better.

    A proper scoring rule: it is minimised only by the true probability, so it
    cannot be gamed by a model that hedges toward the base rate.
    """
    if not predictions:
        return 0.0
    return sum((p - y) ** 2 for p, y in zip(predictions, labels)) / len(predictions)


def reliability(
    predictions: list[float], labels: list[int], *, bins: int = 10
) -> list[dict]:
    """What the model said against what happened, in equal-count bins.

    Equal-count rather than equal-width because predictions at this base rate
    bunch below 0.25, and ten equal-width bins would put nearly everything in
    the first one and report a confident average over an empty range.
    """
    if not predictions:
        return []
    order = sorted(range(len(predictions)), key=lambda i: predictions[i])
    size = max(1, len(order) // bins)
    out = []
    for start in range(0, len(order), size):
        chunk = order[start : start + size]
        if not chunk:
            continue
        predicted = sum(predictions[i] for i in chunk) / len(chunk)
        observed = sum(labels[i] for i in chunk) / len(chunk)
        out.append(
            {
                "n": len(chunk),
                "predicted": predicted,
                "observed": observed,
                "gap": predicted - observed,
            }
        )
    return out


def expected_calibration_error(
    predictions: list[float], labels: list[int], *, bins: int = 10
) -> float:
    """Average absolute gap between what was promised and what happened."""
    table = reliability(predictions, labels, bins=bins)
    total = sum(row["n"] for row in table)
    if not total:
        return 0.0
    return sum(row["n"] * abs(row["gap"]) for row in table) / total


def calibration_noise_floor(
    predictions: list[float], labels: list[int], *, bins: int = 10
) -> float:
    """The ECE a *perfectly* calibrated model would score on this much data.

    Worth stating plainly, because getting it wrong throws away good models. ECE
    compares a bin's predicted rate against the rate observed in that bin, and
    the observed rate is a binomial proportion over a few dozen rows. At a 7%
    base rate a bin of sixty holds about four positives, so it scatters by
    two or three points *whatever the model does*. A fixed threshold therefore
    fails a flawless model on a thin dataset, and - worse in a business that
    will collect more data next quarter - it passes a worse model later purely
    because the bins got fatter.

    So the floor is derived rather than assumed: for a proportion, the expected
    absolute deviation is `sqrt(2p(1-p)/(pi*n))`, averaged over bins by weight.
    An ECE at the floor means the model is as calibrated as this quantity of
    data can demonstrate, which is the strongest true statement available.
    """
    table = reliability(predictions, labels, bins=bins)
    total = sum(row["n"] for row in table)
    if not total:
        return 0.0
    floor = 0.0
    for row in table:
        p = min(max(row["observed"], 1e-6), 1 - 1e-6)
        floor += row["n"] * math.sqrt(2 * p * (1 - p) / (math.pi * row["n"]))
    return floor / total


def auc(predictions: list[float], labels: list[int]) -> float:
    """Rank-based AUC, ties handled at half credit.

    Discrimination only. Reported next to the calibration numbers rather than
    instead of them: a model can order every order correctly and still be
    useless to a solver that needs the magnitude.
    """
    positives = [p for p, y in zip(predictions, labels) if y == 1]
    negatives = [p for p, y in zip(predictions, labels) if y == 0]
    if not positives or not negatives:
        return 0.5
    ordered = sorted(zip(predictions, labels))
    rank_sum = 0.0
    index = 0
    rank = 1
    while index < len(ordered):
        stop = index
        while stop + 1 < len(ordered) and ordered[stop + 1][0] == ordered[index][0]:
            stop += 1
        average_rank = (rank + (rank + (stop - index))) / 2
        for position in range(index, stop + 1):
            if ordered[position][1] == 1:
                rank_sum += average_rank
        rank += stop - index + 1
        index = stop + 1
    n_pos, n_neg = len(positives), len(negatives)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def censoring_adjusted(probability: float, *, observation_rate: float) -> float:
    """What the model would have said if we saw every consequence.

    One division, capped at 1. Reported beside the raw figure, never in place of
    it - see the module docstring. The divisor is an assumption today; the thing
    that would make it a measurement is `REC-2`.
    """
    if observation_rate <= 0:
        raise ValueError("observation rate must be positive")
    return min(1.0, probability / observation_rate)
