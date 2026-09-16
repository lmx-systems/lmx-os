"""A promise with a guarantee behind it (`MODEL_AND_DATA_BRIEF.md` rule 4).

> *"Conformal intervals before any promise. A point estimate with a hopeful p90
> is not an SLA. A distribution-free coverage guarantee is."*

The failure this exists to prevent is already recorded in rule (3): the p90
model **covered 66.5%** of cold-start cases after promising 90%. A quantile
model fits the ninetieth percentile *of the training data*; nothing makes it the
ninetieth percentile of anything else. Split conformal adds the missing step -
measure how wrong the quantile was on data the model never saw, and widen it by
that much.

**What is guaranteed, exactly.** With `n` calibration residuals and the
`k`-th smallest taken as the adjustment, `P(Y <= prediction) >= 1 - alpha` over
a fresh draw from the same distribution. No assumption about the model, the
noise or the shape - only that the calibration and test points are
exchangeable. Finite-sample, not asymptotic.

**And that assumption is exactly what cold start breaks.** A dock nobody has
visited is not exchangeable with the docks in the calibration set: the model's
residuals there have a different distribution, so the guarantee does not
transfer. Calibrating on warm receivers and promising on cold ones reproduces
the original failure with more arithmetic in front of it. The fix is to
calibrate on held-out *receivers* when the promise is for a new customer, which
is the same lesson `ml/m2/gates.py` learned about isotonic calibration - a
correction fitted on the wrong population is worse than no correction.
`evaluate.py` measures both ways rather than asserting either.

**Small calibration sets cannot make large promises.** At `alpha = 0.10` the
adjustment is the ceil((n+1) x 0.90)-th smallest residual, and when that index
exceeds `n` there is no finite value that delivers the guarantee - the honest
answer is "not with this much data", not a number. Nine residuals is the floor
for 90%, ninety-nine for 99%, and a harness that silently clamped instead would
be quoting a guarantee it had not earned.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


class NotEnoughCalibrationData(RuntimeError):
    """Raised when no finite interval can carry the requested guarantee."""


def minimum_calibration_size(alpha: float) -> int:
    """Smallest calibration set for which a 1-alpha bound exists at all.

    Needs ceil((n+1)(1-alpha)) <= n, which is n >= ceil(1/alpha) - 1.
    """
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1")
    return math.ceil(1 / alpha) - 1


@dataclass
class ConformalUpperBound:
    """A one-sided bound: how much to add so the promise is kept 1-alpha of the
    time."""

    alpha: float
    adjustment: float
    calibration_size: int

    @classmethod
    def fit(
        cls, actual: list[float], predicted: list[float], *, alpha: float = 0.10
    ) -> ConformalUpperBound:
        """Calibrate on residuals from data the model did not train on."""
        n = len(actual)
        floor = minimum_calibration_size(alpha)
        if n < floor:
            raise NotEnoughCalibrationData(
                f"{n} calibration points cannot support a {1 - alpha:.0%} "
                f"guarantee; {floor} is the minimum. Widening a bound to "
                "infinity is the only honest alternative, so this refuses rather "
                "than returning a number that looks like a promise"
            )
        # One-sided conformity score: how far the outcome overshot the
        # prediction. Negative where the prediction was already generous.
        scores = sorted(y - p for y, p in zip(actual, predicted))
        index = math.ceil((n + 1) * (1 - alpha)) - 1
        return cls(
            alpha=alpha, adjustment=scores[min(index, n - 1)], calibration_size=n
        )

    def apply(self, predicted: list[float]) -> list[float]:
        return [p + self.adjustment for p in predicted]

    def __str__(self) -> str:
        direction = "widened" if self.adjustment > 0 else "tightened"
        return (
            f"{1 - self.alpha:.0%} conformal bound: {direction} by "
            f"{self.adjustment:+.2f} min on {self.calibration_size} calibration "
            "residuals"
        )
