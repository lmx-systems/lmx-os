"""The baseline, and something that has to beat it (rule 3).

> *"Beat the baseline on both populations, or ship the baseline. On our own
> harness the shrinkage baseline beat gradient boosting."*

That last clause is the reason this file leads with the baseline rather than
treating it as a formality. It has already won once on this team's data. A
harness that assumes the clever model wins and only measures by how much is not
measuring the thing that decides what ships.

**The baseline is one column.** A location's own consequence rate, shrunk
toward its node class - no fitting, no hyperparameters, nothing to overfit, and
it degrades gracefully to the class rate on a dock nobody has seen. It is hard
to beat for the same reason it is hard to improve: at this base rate and this
volume, most of the available signal is "what does this dock usually do".

**The challenger here is logistic, not boosted.** `MODEL_AND_DATA_BRIEF.md` §M2
specifies a gradient-boosted classifier, and that is what will eventually ship.
It is not what belongs in this file, because a harness whose checks only run
when `lightgbm` is installed is a harness that stops running the day CI's
dependency cache changes. Everything here is stdlib, so every gate runs
everywhere; the boosted model plugs in at `fit`/`predict` and is scored by the
same code. If the tree cannot beat forty lines of gradient descent on the same
split, that is a finding worth having cheaply.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.identity.node_class import NODE_CLASSES
from ml.m2.features import FeatureRow


class ShrinkageBaseline:
    """Predict the dock's own history, shrunk toward its node class.

    Nothing is fitted: the feature builder already did the only arithmetic
    involved, on the past only. Which is also the point - a baseline with no
    training step cannot leak, so it is the one prediction in this file that is
    trustworthy by construction.
    """

    name = "shrinkage"

    def fit(self, rows: list[FeatureRow]) -> ShrinkageBaseline:
        return self

    def predict(self, rows: list[FeatureRow]) -> list[float]:
        return [r.shrunk_location_rate for r in rows]


def feature_vector(row: FeatureRow, *, leak: float | None = None) -> list[float]:
    """The design matrix, one row.

    `leak` is the forbidden whole-file location rate, threaded through as an
    explicit argument rather than a flag on the model. A leak you have to pass
    by name at the call site is one somebody has to choose.
    """
    one_hot = [1.0 if row.node_class == c else 0.0 for c in NODE_CLASSES]
    base = [
        row.shrunk_location_rate,
        row.node_class_rate,
        row.log_order_value,
        (row.hour_of_day - 12) / 6.0,
        math.log1p(row.location_late_so_far),
        1.0 if row.day_of_week == 4 else 0.0,  # Friday: no recovery before Monday
    ]
    if leak is not None:
        base.append(leak)
    return base + one_hot


@dataclass
class LogisticModel:
    """Plain logistic regression, fitted by gradient descent.

    Standardised inputs and an L2 penalty, because several features here are
    near-collinear (a dock's shrunk rate and its class rate agree exactly on any
    dock with no history) and an unpenalised fit on those oscillates.
    """

    name: str = "logistic"
    epochs: int = 220
    learning_rate: float = 0.35
    l2: float = 1e-3
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    means: list[float] = field(default_factory=list)
    scales: list[float] = field(default_factory=list)
    use_leak: bool = False
    _leak: dict[str, float] = field(default_factory=dict)

    def with_leak(self, leak: dict[str, float]) -> LogisticModel:
        """Return a copy that will use the forbidden feature. For `gates.py`."""
        return LogisticModel(
            name="logistic+leak",
            epochs=self.epochs,
            learning_rate=self.learning_rate,
            l2=self.l2,
            use_leak=True,
            _leak=leak,
        )

    def _matrix(self, rows: list[FeatureRow]) -> list[list[float]]:
        if not self.use_leak:
            return [feature_vector(r) for r in rows]
        return [feature_vector(r, leak=self._leak.get(r.location_id, 0.0)) for r in rows]

    def fit(self, rows: list[FeatureRow]) -> LogisticModel:
        matrix = self._matrix(rows)
        labels = [r.label for r in rows]
        if not matrix:
            return self
        width = len(matrix[0])
        self.means = [sum(col) / len(col) for col in zip(*matrix)]
        self.scales = [
            max(
                math.sqrt(
                    sum((v - self.means[i]) ** 2 for v in col) / len(col)
                ),
                1e-6,
            )
            for i, col in enumerate(zip(*matrix))
        ]
        scaled = [
            [(row[i] - self.means[i]) / self.scales[i] for i in range(width)]
            for row in matrix
        ]
        self.weights = [0.0] * width
        # Start the intercept at the empirical log-odds so descent begins
        # calibrated rather than spending epochs walking up from 0.5 - which at
        # a 7% base rate is a long way up.
        rate = min(max(sum(labels) / len(labels), 1e-4), 1 - 1e-4)
        self.bias = math.log(rate / (1 - rate))

        n = len(scaled)
        for _ in range(self.epochs):
            grad_w = [0.0] * width
            grad_b = 0.0
            for row, label in zip(scaled, labels):
                z = self.bias + sum(w * x for w, x in zip(self.weights, row))
                error = _sigmoid(z) - label
                grad_b += error
                for i, x in enumerate(row):
                    grad_w[i] += error * x
            self.bias -= self.learning_rate * grad_b / n
            for i in range(width):
                self.weights[i] -= self.learning_rate * (
                    grad_w[i] / n + self.l2 * self.weights[i]
                )
        return self

    def predict(self, rows: list[FeatureRow]) -> list[float]:
        if not self.weights:
            return [0.0] * len(rows)
        out = []
        for row in self._matrix(rows):
            scaled = [
                (row[i] - self.means[i]) / self.scales[i] for i in range(len(row))
            ]
            out.append(
                _sigmoid(self.bias + sum(w * x for w, x in zip(self.weights, scaled)))
            )
        return out


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


@dataclass
class CalibratedModel:
    """Any model, plus isotonic calibration fitted on held-back rows.

    `MODEL_AND_DATA_BRIEF.md` §M2 asks for the classifier *and* the calibration,
    and the second half is the half that matters to the solver. A logistic fit -
    or a boosted tree - on a world with per-dock random effects is misspecified,
    and a misspecified model's probabilities are ordered correctly and priced
    wrongly. Isotonic fixes the pricing without touching the ordering, which is
    exactly the trade to make: monotone, so it can never reorder two orders, and
    non-parametric, so it does not assume the miscalibration has a shape.

    **The calibration slice comes out of training, never out of test.** Fitting
    the calibrator on the data it is scored against would make any model look
    perfectly calibrated, which is the same class of mistake as rule (1) and
    just as invisible in the numbers.

    **And the slice has to resemble where the model will be used.** This is the
    part that is easy to get wrong, and it produced the clearest result on the
    synthetic corpus: a calibrator fitted on the most recent training rows
    improves calibration on next month and *degrades* it on docks nobody has
    visited - from 0.74x the noise floor to 1.94x, so the correction is worse
    than no correction. The mapping is learned on docks with history, where
    scores spread out; it is applied on docks without, where they bunch near the
    class prior. Monotone or not, it is the wrong curve.

    So the slice is a strategy rather than a constant:

    `recent`            the tail of the training period. Right for a model
                        serving customers we already deliver for.
    `unseen_locations`  a hash-held-out set of docks inside training, scored by
                        a base model that never saw them. Right for week one at
                        a new customer, which is the number a prospect is
                        actually buying.

    Picking between them is a deployment decision, not a tuning knob: it asks
    which population the model will price for. Both are reported by `gates.py`
    so the choice is made in the open.
    """

    base: object
    calibration_fraction: float = 0.25
    slice_strategy: str = "recent"
    calibrator: object = None

    @property
    def name(self) -> str:
        return f"{getattr(self.base, 'name', 'model')}+isotonic[{self.slice_strategy}]"

    def _slice(
        self, rows: list[FeatureRow]
    ) -> tuple[list[FeatureRow], list[FeatureRow]]:
        if self.slice_strategy == "unseen_locations":
            # A different salt from splits.held_out_locations, so the inner
            # holdout cannot accidentally be the outer one - which would fit the
            # calibrator on the very docks it is about to be scored against.
            held = {
                r.location_id
                for r in rows
                if _hash_fraction("m2-calib:", r.location_id) < self.calibration_fraction
            }
            return (
                [r for r in rows if r.location_id not in held],
                [r for r in rows if r.location_id in held],
            )
        ordered = sorted(rows, key=lambda r: (r.delivered_on, r.order_id))
        cut = int(len(ordered) * (1 - self.calibration_fraction))
        return ordered[:cut], ordered[cut:]

    def fit(self, rows: list[FeatureRow]) -> CalibratedModel:
        from ml.m2.calibration import IsotonicCalibrator

        ordered = sorted(rows, key=lambda r: (r.delivered_on, r.order_id))
        fit_rows, calibration_rows = self._slice(ordered)
        if not fit_rows or not calibration_rows:
            self.base.fit(ordered)
            self.calibrator = None
            return self
        self.base.fit(fit_rows)
        self.calibrator = IsotonicCalibrator.fit(
            self.base.predict(calibration_rows),
            [r.label for r in calibration_rows],
        )
        return self

    def predict(self, rows: list[FeatureRow]) -> list[float]:
        raw = self.base.predict(rows)
        if self.calibrator is None:
            return raw
        return [self.calibrator.transform(score) for score in raw]


def _hash_fraction(salt: str, key: str) -> float:
    """Stable [0, 1) position for a key. Same dock, same side, every run."""
    import hashlib

    digest = hashlib.sha256(f"{salt}{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)
