"""Scoring dwell on three populations, and only promising what holds (`PRD-4`).

Promoted from `lmx-dwell/dwell/train.py` - *"promote it. Losing on unseen
receivers blocks release"* - with the LightGBM challenger left in the analysis
project for now and conformal calibration added, which the original did not
have and which rule (4) requires before any promise.

**Three populations, because one average hides the failure that matters.**
Warm receivers are docks the model has seen. Cold receivers are docks it has
not, which is every dock at a new customer. A single held-out number is mostly
warm, so it reports the easy case and buries the one a prospect is buying.

**Stops in, not rows (the brief's gate 5).** `run` takes the book and decides
who is held out before a single feature exists. Handed rows built over the whole
book, it split them afterwards, and a held-out dock's earlier visits had
already become its history: 66 of the real export's 68 cold rows carried it
into the test. The baseline never reads history, so its cold number was honest;
the challenger's was warm.

**`k` comes from the data (gate 3),** chosen per quantile on training days by
`estimate_prior_strength`, and the challenger's history is shrunk with the same
`k` as the baseline it must beat.

**Every score carries an interval over docks (gate 4).** The real export's cold
population is fourteen docks. A difference in the third decimal between two
models on fourteen docks is not a result, and the interval is how the report
says so instead of leaving a reader to guess.

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
import random
from dataclasses import dataclass, field

from ml.m1.baseline import (
    PRIOR_STRENGTH_GRID,
    GlobalQuantile,
    ShrunkQuantileBaseline,
    coverage,
    estimate_prior_strength,
    pinball_loss,
)
from ml.m1.conformal import ConformalUpperBound, NotEnoughCalibrationData
from ml.m1.features import (
    DwellRow,
    build,
    chronological_cut,
    coldstart_split,
    is_held_out,
    time_split,
)
from ml.real.export import DetailStop, usable_dwell

PLANNING_QUANTILE = 0.5
PROMISE_QUANTILE = 0.9
# Resampling runs on per-dock totals, so the cost grows with docks, not stops,
# and a thousand draws is cheap enough to keep a 95% percentile interval steady.
BOOTSTRAP_RESAMPLES = 1000
# Below this many docks a population's point estimates are not worth quoting
# without their intervals, and the report says so.
FEW_DOCKS = 30


@dataclass
class Score:
    model: str
    population: str
    n: int
    pinball: float
    coverage: float
    # Docks, not stops - the number that decides how far to trust the two above.
    receivers: int = 0
    # 95% intervals from resampling whole docks. None with fewer than two docks,
    # where resampling can only hand back the one number it was given.
    pinball_interval: tuple[float, float] | None = None
    coverage_interval: tuple[float, float] | None = None


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
    # The shrinkage `k` the training days chose, per quantile.
    prior_strength: dict[str, float] = field(default_factory=dict)

    def challenger_verdict(self) -> tuple[bool, list[str]]:
        """`PRD-5`: beats the baseline on both populations, or ships the baseline.

        Both, and at every quantile. Not on average, and not on the headline
        split - `MODEL_AND_DATA_BRIEF.md` rule (3) exists because the p90 model
        covered 66.5% of cold-start cases after promising 90%, and an average
        across warm and cold would have hidden exactly that.

        Returns (ships, populations it lost on). An empty loss list with no
        challenger scored is not a pass: nothing was tested.
        """
        challenger_scores = [
            s for s in self.scores if s.model.startswith("sklearn-")
        ]
        if not challenger_scores:
            return False, ["no challenger was scored"]
        lost = []
        for score in challenger_scores:
            baseline = next(
                (
                    s for s in self.scores
                    if s.population == score.population
                    and s.model == "shrunk-quantile"
                ),
                None,
            )
            if baseline is None or score.pinball >= baseline.pinball:
                lost.append(score.population)
        return not lost, sorted(lost)

    def inside_the_noise(self) -> list[str]:
        """Populations where the challenger's 95% interval overlaps the baseline's.

        A conservative reading - two overlapping intervals can still hide a
        real paired difference - but it is the honest one for a report: a win
        these intervals do not clear has not been shown to be a win. On the real
        export it is three of the four, and switching off any one of the gate
        fixes turns warm/p90 back into a loss.
        """
        tied = []
        for score in self.scores:
            if not score.model.startswith("sklearn-"):
                continue
            baseline = next(
                (
                    s for s in self.scores
                    if s.population == score.population
                    and s.model == "shrunk-quantile"
                ),
                None,
            )
            if (
                baseline is None
                or score.pinball_interval is None
                or baseline.pinball_interval is None
            ):
                continue
            low, high = score.pinball_interval
            baseline_low, baseline_high = baseline.pinball_interval
            if low <= baseline_high and baseline_low <= high:
                tied.append(score.population)
        return sorted(tied)

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


def populations(
    stops: list[DetailStop], test_fraction: float = 0.2
) -> tuple[list[DwellRow], list[DwellRow], list[DwellRow], str]:
    """Train, warm test and cold test, held out before any feature is built.

    The cut is chosen on the calendar of usable stops and the held-out docks by
    hash, and only then does `build` run - told that a held-out dock's visits
    before the cut are stops the model has never seen. Returns
    (train, warm, cold, cut day).
    """
    days = [
        s.arrived.date().isoformat() for s in usable_dwell(stops) if s.arrived is not None
    ]
    if not days:
        return [], [], [], ""
    cut = chronological_cut(days, test_fraction)

    def unseen(stop: DetailStop) -> bool:
        return (
            stop.arrived is not None
            and stop.arrived.date().isoformat() < cut
            and is_held_out(stop.receiver_id)
        )

    train_all, test, _ = time_split(build(stops, unseen=unseen), cut=cut)
    train, warm, cold = coldstart_split(train_all, test)
    return train, warm, cold, cut


def _percentile_interval(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    top = len(ordered) - 1
    return (
        round(ordered[int(0.025 * top)], 4),
        round(ordered[math.ceil(0.975 * top)], 4),
    )


def _dock_intervals(
    rows: list[DwellRow],
    predicted: list[float],
    q: float,
    *,
    seed: str,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """95% intervals for pinball loss and coverage, resampling docks.

    Stops at one dock are not independent draws - they share the dock - so
    resampling stops would report the precision of 68 observations when the
    cold population is fourteen docks. Resampling whole docks reports the
    precision there actually is. Seeded from the population and model, so a
    rerun prints the same interval.
    """
    per_dock: dict[str, list[float]] = {}
    for row, p in zip(rows, predicted):
        cell = per_dock.setdefault(row.receiver_id, [0.0, 0.0, 0.0])
        cell[0] += pinball_loss([row.dwell_min], [p], q)
        cell[1] += 1.0 if row.dwell_min <= p else 0.0
        cell[2] += 1.0
    docks = [per_dock[k] for k in sorted(per_dock)]
    if len(docks) < 2:
        return None, None

    rng = random.Random(seed)
    losses: list[float] = []
    kept: list[float] = []
    for _ in range(resamples):
        loss = hit = count = 0.0
        for _ in range(len(docks)):
            cell = docks[rng.randrange(len(docks))]
            loss += cell[0]
            hit += cell[1]
            count += cell[2]
        losses.append(loss / count)
        kept.append(hit / count)
    return _percentile_interval(losses), _percentile_interval(kept)


def _score(model, rows: list[DwellRow], q: float, population: str) -> Score:
    predicted = model.predict(rows)
    actual = [r.dwell_min for r in rows]
    pinball_interval, coverage_interval = _dock_intervals(
        rows, predicted, q, seed=f"{population}|{model.name}"
    )
    return Score(
        model=model.name,
        population=population,
        n=len(rows),
        pinball=round(pinball_loss(actual, predicted, q), 4),
        coverage=round(coverage(actual, predicted), 4),
        receivers=len({r.receiver_id for r in rows}),
        pinball_interval=pinball_interval,
        coverage_interval=coverage_interval,
    )


def _promise(
    fit_rows: list[DwellRow],
    calibration_rows: list[DwellRow],
    test_rows: list[DwellRow],
    *,
    population: str,
    calibrated_on: str,
    prior_strength: float,
) -> Promise | None:
    """Fit a p90, conformalise it on one population, promise it on another."""
    if not fit_rows or not calibration_rows or not test_rows:
        return None
    model = ShrunkQuantileBaseline(
        q=PROMISE_QUANTILE, prior_strength=prior_strength
    ).fit(fit_rows)

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


def run(stops: list[DetailStop], *, with_challenger: bool = False) -> Evaluation:
    """Score the baseline, and optionally the `PRD-5` challenger, on both
    populations.

    `with_challenger` is off by default so the harness keeps running where no ML
    stack is installed - which is CI, and which is the whole reason the rest of
    `ml/m1/` is standard library.
    """
    train, warm, cold, cut = populations(stops)

    evaluation = Evaluation(cut_day=cut)
    if not train or not (warm or cold):
        evaluation.notes.append("not enough history to split chronologically")
        return evaluation

    challenger_available = False
    if with_challenger:
        from ml.m1.challenger import is_available

        challenger_available = is_available()
        if not challenger_available:
            from ml.m1.challenger import BLOCKED_REASON

            evaluation.notes.append(
                f"no challenger was scored: {BLOCKED_REASON}"
            )

    for q, label in ((PLANNING_QUANTILE, "p50"), (PROMISE_QUANTILE, "p90")):
        k = estimate_prior_strength(train, q)
        evaluation.prior_strength[label] = k
        shrunk = ShrunkQuantileBaseline(q=q, prior_strength=k).fit(train)
        flat = GlobalQuantile(q=q).fit(train)
        models: list = [shrunk, flat]
        if challenger_available:
            from ml.m1.challenger import GradientBoostedQuantile

            models.append(GradientBoostedQuantile(q=q, prior_strength=k).fit(train))
        for population, subset in (("warm", warm), ("cold", cold)):
            if not subset:
                continue
            for model in models:
                score = _score(model, subset, q, f"{population}/{label}")
                evaluation.scores.append(score)

    promise_k = evaluation.prior_strength["p90"]

    # The warm promise: calibrate on the most recent slice of training history.
    inner_train, inner_calibration, _ = time_split(train, 0.2)
    warm_promise = _promise(
        inner_train, inner_calibration, warm,
        population="warm", calibrated_on="recent training days",
        prior_strength=promise_k,
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
            prior_strength=promise_k,
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
            prior_strength=promise_k,
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

    for label, k in evaluation.prior_strength.items():
        if k >= max(PRIOR_STRENGTH_GRID):
            evaluation.notes.append(
                f"{label}: the data chose the largest shrinkage on offer (k={k:g}) - "
                "on these days a dock's own history tells the model little beyond "
                "its location type"
            )

    cold_docks = len({r.receiver_id for r in cold})
    if cold and cold_docks < FEW_DOCKS:
        evaluation.notes.append(
            f"the cold population is {cold_docks} docks ({len(cold)} stops). Read "
            "its intervals before its numbers: a difference inside them is not a "
            "difference"
        )

    if with_challenger and challenger_available:
        ships, lost_on = evaluation.challenger_verdict()
        if ships:
            verdict = (
                "PRD-5: the challenger beats the baseline on every population - it "
                "may ship"
            )
            tied = evaluation.inside_the_noise()
            if tied:
                verdict += (
                    ". But its interval overlaps the baseline's on "
                    + ", ".join(tied)
                    + ": those wins are point estimates, and rule (3) as written "
                    "does not ask for more"
                )
        else:
            verdict = (
                "PRD-5: the challenger lost on " + ", ".join(lost_on)
                + " - ship the baseline"
            )
        evaluation.notes.append(verdict)

    losses = evaluation.baseline_wins()
    if losses:
        evaluation.notes.append(
            "the shrunk baseline did not beat a single global quantile on "
            + ", ".join(losses)
            + " - per-dock structure is not carrying information there"
        )
    return evaluation
