"""Scoring dwell on three populations, and only promising what holds (`PRD-4`).

Promoted from `lmx-dwell/dwell/train.py` - *"promote it. Losing on unseen
receivers blocks release"* - with the LightGBM challenger left in the analysis
project for now and conformal calibration added, which the original did not
have and which rule (4) requires before any promise.

**Three populations, because one average hides the failure that matters.**
Warm receivers are docks the model has seen. Cold receivers are docks it has
not, which is every dock at a new customer. A single held-out number is mostly
warm, so it reports the easy case and buries the one a prospect is buying.

**Release rule, from `MODEL_AND_DATA_BRIEF.md` rule (3):** beat the baseline on
both populations or ship the baseline. Not "on average", and not "on the
headline split".

**The calibration slice is carved out of training, twice over.** Once
chronologically for the warm promise, once by held-out receivers for the cold
one. Calibrating on the test set would make any bound look perfect, and
calibrating warm-and-promising-cold is the exact shape of the 66.5% failure.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ml.m1.baseline import (
    GlobalQuantile,
    ShrunkQuantileBaseline,
    coverage,
    pinball_loss,
)
from ml.m1.conformal import ConformalUpperBound, NotEnoughCalibrationData
from ml.m1.features import DwellRow, coldstart_split, time_split

PLANNING_QUANTILE = 0.5
PROMISE_QUANTILE = 0.9


@dataclass
class Score:
    model: str
    population: str
    n: int
    pinball: float
    coverage: float


@dataclass
class Promise:
    """A p90 before and after conformal calibration, with what it cost."""

    population: str
    calibrated_on: str
    n: int
    raw_coverage: float
    conformal_coverage: float
    adjustment_minutes: float
    raw_promise_minutes: float
    conformal_promise_minutes: float
    # Whether the observed coverage is consistent with the guarantee, allowing
    # for how few points it was measured on. A hard `>= 0.90` would fail a
    # correct bound that happened to land on 0.898 across 254 stops, which is
    # well inside binomial noise - the same trap `ml/m2/gates.py` fell into with
    # a fixed calibration threshold. The guarantee is about expectation; the
    # measurement is a proportion and has a standard error.
    holds: bool
    coverage_floor: float


@dataclass
class Evaluation:
    cut_day: str
    scores: list[Score] = field(default_factory=list)
    promises: list[Promise] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def baseline_wins(self) -> list[str]:
        """Populations where the shrunk baseline did not beat the constant."""
        losses = []
        for population in {s.population for s in self.scores}:
            here = {s.model: s for s in self.scores if s.population == population}
            shrunk = here.get("shrunk-quantile")
            flat = here.get("global-quantile")
            if shrunk and flat and shrunk.pinball >= flat.pinball:
                losses.append(population)
        return sorted(losses)


def _score(model, rows: list[DwellRow], q: float, population: str) -> Score:
    predicted = model.predict(rows)
    actual = [r.dwell_min for r in rows]
    return Score(
        model=model.name,
        population=population,
        n=len(rows),
        pinball=round(pinball_loss(actual, predicted, q), 4),
        coverage=round(coverage(actual, predicted), 4),
    )


def _promise(
    fit_rows: list[DwellRow],
    calibration_rows: list[DwellRow],
    test_rows: list[DwellRow],
    *,
    population: str,
    calibrated_on: str,
) -> Promise | None:
    """Fit a p90, conformalise it on one population, promise it on another."""
    if not fit_rows or not calibration_rows or not test_rows:
        return None
    model = ShrunkQuantileBaseline(q=PROMISE_QUANTILE).fit(fit_rows)

    calibration_predicted = model.predict(calibration_rows)
    try:
        bound = ConformalUpperBound.fit(
            [r.dwell_min for r in calibration_rows],
            calibration_predicted,
            alpha=1 - PROMISE_QUANTILE,
        )
    except NotEnoughCalibrationData:
        return None

    raw = model.predict(test_rows)
    adjusted = bound.apply(raw)
    actual = [r.dwell_min for r in test_rows]
    conformal_coverage = coverage(actual, adjusted)
    floor = PROMISE_QUANTILE - 1.96 * math.sqrt(
        PROMISE_QUANTILE * (1 - PROMISE_QUANTILE) / len(test_rows)
    )
    return Promise(
        population=population,
        calibrated_on=calibrated_on,
        n=len(test_rows),
        raw_coverage=round(coverage(actual, raw), 4),
        conformal_coverage=round(conformal_coverage, 4),
        adjustment_minutes=round(bound.adjustment, 3),
        raw_promise_minutes=round(sum(raw) / len(raw), 2),
        conformal_promise_minutes=round(sum(adjusted) / len(adjusted), 2),
        holds=conformal_coverage >= floor,
        coverage_floor=round(floor, 4),
    )


def run(rows: list[DwellRow]) -> Evaluation:
    train_all, test, cut = time_split(rows)
    train, warm, cold = coldstart_split(train_all, test)

    evaluation = Evaluation(cut_day=cut)
    if not train or not test:
        evaluation.notes.append("not enough history to split chronologically")
        return evaluation

    for q, label in ((PLANNING_QUANTILE, "p50"), (PROMISE_QUANTILE, "p90")):
        shrunk = ShrunkQuantileBaseline(q=q).fit(train)
        flat = GlobalQuantile(q=q).fit(train)
        for population, subset in (("warm", warm), ("cold", cold)):
            if not subset:
                continue
            for model in (shrunk, flat):
                score = _score(model, subset, q, f"{population}/{label}")
                evaluation.scores.append(score)

    # The warm promise: calibrate on the most recent slice of training history.
    inner_train, inner_calibration, _ = time_split(train, 0.2)
    warm_promise = _promise(
        inner_train, inner_calibration, warm,
        population="warm", calibrated_on="recent training days",
    )
    if warm_promise:
        evaluation.promises.append(warm_promise)

    # The cold promise, calibrated the wrong way on purpose: on warm receivers.
    # This is the 66.5% failure, reproduced deliberately so the report shows what
    # the tempting shortcut actually delivers.
    if cold:
        wrong = _promise(
            inner_train, inner_calibration, cold,
            population="cold", calibrated_on="recent training days (warm docks)",
        )
        if wrong:
            evaluation.promises.append(wrong)

        # And the right way: calibrate on receivers the fitted model never saw.
        held_train, _, held_calibration = coldstart_split(
            train, train, holdout_fraction=0.25
        )
        right = _promise(
            held_train, held_calibration, cold,
            population="cold", calibrated_on="held-out receivers",
        )
        if right:
            evaluation.promises.append(right)

    for promise in evaluation.promises:
        widening = promise.conformal_promise_minutes - promise.raw_promise_minutes
        if promise.holds and widening > 5.0:
            evaluation.notes.append(
                f"the {promise.population} promise calibrated on "
                f"{promise.calibrated_on} is kept "
                f"({promise.conformal_coverage:.1%}) by quoting "
                f"{promise.conformal_promise_minutes:.1f} minutes for a stop whose "
                f"median is about two. The guarantee is real and the number is "
                "not sellable - which is what not having enough cold-start data "
                "looks like once the arithmetic is done honestly"
            )

    losses = evaluation.baseline_wins()
    if losses:
        evaluation.notes.append(
            "the shrunk baseline did not beat a single global quantile on "
            + ", ".join(losses)
            + " - per-dock structure is not carrying information there"
        )
    return evaluation
