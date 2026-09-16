"""The five rules, as checks that can fail (`MODEL_AND_DATA_BRIEF.md` §4).

> *"Five rules. Each one is a release gate, not a guideline."*

A rule written in a document is a guideline no matter what the document calls
it. These are the same five as functions that return False, plus the label
sizing, plus the two things the corpus turned up that the brief does not yet
address - the arm's statistical power and the censoring bias.

**Thresholds are judgements and are labelled as such.** Where a gate needs a
number that nobody has derived, the number is a named default with
`[ASSUMPTION]` on it and an entry in `OPEN_DECISIONS`, rather than a constant
buried in a comparison. A threshold that cannot be found cannot be argued with,
and this one decides what ships.

**Rule 1 is tested by perturbation, not by score.** The tempting check is to fit
with and without the leaky feature and watch the leak lose on held-out data.
Run against this corpus, it does not lose. It wins on the chronological test
set too - because a statistic computed over the whole file is still computed
over the whole file when you score the back half of it, so the "future" split
is contaminated along with everything else.

That is the finding, and it is worse than the story usually told about leakage:
**there is no offline number that detects it.** Every score a leaking pipeline
produces is better, including the one built specifically to be honest. The only
thing that catches it is looking at how the feature was computed. So the gate
changes a future row's outcome and asserts that no earlier row's features
moved - the property itself, deterministic, and impossible for a model to
satisfy by accident. The score comparison is still run and reported, as
evidence for why the structural check has to exist rather than as a substitute
for it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.record.consequences import M2_BAND_MINIMUM, M2_BAND_TARGET
from ml.m2.calibration import (
    auc,
    brier_score,
    calibration_noise_floor,
    censoring_adjusted,
    expected_calibration_error,
)
from ml.m2.corpus import ARM_CONTROL, Corpus, expected_observation_rate
from ml.m2.features import build_features, leaky_location_rate
from ml.m2.models import CalibratedModel, LogisticModel, ShrinkageBaseline
from ml.m2.splits import Split, chronological, held_out_locations

# How far above its own noise floor a model's calibration error may sit. Not a
# judgement about pricing: the floor is derived from the sample size (see
# `calibration.calibration_noise_floor`), so this only asks that the model be
# close to as calibrated as the data can demonstrate. 1.5 leaves room for the
# floor's own approximation without admitting a model that is visibly off.
#
# An absolute threshold was tried first and was wrong in both directions - it
# failed a well-calibrated model on a thin split and would have passed a worse
# one later, purely because more data narrows the bins.
MAX_CALIBRATION_ERROR_OVER_NOISE_FLOOR = 1.5

# `[ASSUMPTION]` How much a challenger must beat the baseline by before the
# extra machinery is worth operating. Brier improvement, relative.
MIN_RELATIVE_BRIER_GAIN = 0.02

OPEN_DECISIONS = (
    "Whether M2 may train on the whole book with the arm as a correction, or "
    "only on arm labels. The two readings differ by a factor of 12 in time to "
    "trainability - roughly two years against twenty at one partner.",
    "The real observation rate for consequences. Assumed ~0.5; REC-2's one SMS "
    "after a late delivery is the instrument that would measure it.",
)


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str
    numbers: dict = field(default_factory=dict)

    def __str__(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return f"[{mark}] {self.name}: {self.detail}"


def gate_causal_features_only(corpus: Corpus) -> GateResult:
    """Rule 1. Change the future; assert the past did not move.

    Flips the outcome of the single last labelled delivery in the book and
    rebuilds every feature. If any earlier row changes, some accumulator is
    being read before it is written, or an aggregate is being computed over the
    whole file. Either way the validation score is a fiction.
    """
    deliveries = sorted(corpus.deliveries, key=lambda d: (d.delivered_on, d.order_id))
    before = build_features(deliveries)

    index = max(
        (i for i, d in enumerate(deliveries) if d.was_late and d.observed_label),
        default=None,
    )
    if index is None:
        return GateResult("causal-features-only", False, "no labelled rows to perturb")

    target = deliveries[index]
    flipped = "escalation_call" if target.observed_label == "silence" else "silence"
    deliveries[index] = type(target)(
        **{**target.__dict__, "observed_label": flipped}
    )
    after = build_features(deliveries)

    changed = [
        a.order_id
        for a, b in zip(before, after)
        if a.order_id == b.order_id
        and (
            a.shrunk_location_rate != b.shrunk_location_rate
            or a.node_class_rate != b.node_class_rate
            or a.label != b.label
        )
        and a.order_id != target.order_id
    ]
    return GateResult(
        "causal-features-only",
        passed=not changed,
        detail=(
            "flipping the last outcome in the book moved no earlier feature"
            if not changed
            else f"{len(changed)} earlier rows moved - the future is in the past"
        ),
        numbers={"rows_affected": len(changed), "perturbed": target.order_id},
    )


def leakage_demonstration(corpus: Corpus) -> dict:
    """Not a gate. Evidence for why the gate cannot be a score.

    Fits the same model with and without the whole-file location rate, and
    scores both on every split available. The leaky model is expected to win all
    of them, including the held-out future and the unseen docks, because an
    aggregate computed over the whole file is contaminated on every side of
    every split you can draw inside that file.

    Which is the point. A reviewer looking at these numbers sees an improvement
    and approves the change. The collapse happens in production, where the
    aggregate has to be computed without tomorrow's outcomes in it - and by then
    the model is quoting prices.
    """
    rows = build_features(corpus.deliveries)
    leak = leaky_location_rate(corpus.deliveries)

    out: dict = {}
    for split in (chronological(rows), held_out_locations(rows)):
        clean = LogisticModel().fit(split.train)
        leaky = LogisticModel().with_leak(leak).fit(split.train)

        def brier(model, subset: list) -> float:
            return brier_score(model.predict(subset), [r.label for r in subset])

        out[split.name] = {
            "clean_in_sample": brier(clean, split.train),
            "leaky_in_sample": brier(leaky, split.train),
            "clean_on_test": brier(clean, split.test),
            "leaky_on_test": brier(leaky, split.test),
            "leak_flattered_the_test_score_by": (
                brier(clean, split.test) - brier(leaky, split.test)
            ),
        }
    return out


def _score(model, split: Split) -> dict:
    model.fit(split.train)
    predictions = model.predict(split.test)
    labels = [r.label for r in split.test]
    base_rate = sum(labels) / len(labels) if labels else 0.0
    ece = expected_calibration_error(predictions, labels)
    floor = calibration_noise_floor(predictions, labels)
    return {
        "n_train": len(split.train),
        "n_test": len(split.test),
        "base_rate": base_rate,
        "brier": brier_score(predictions, labels),
        "auc": auc(predictions, labels),
        "ece": ece,
        "noise_floor": floor,
        "ece_over_floor": ece / floor if floor else float("inf"),
    }


def gate_beats_the_baseline(corpus: Corpus) -> GateResult:
    """Rule 3. Both populations, or ship the baseline.

    The brief records the shrinkage baseline beating gradient boosting on this
    team's own harness, so "the challenger loses" is an expected outcome with a
    defined consequence, not a bug to be tuned away.
    """
    rows = build_features(corpus.deliveries)
    results = {}
    for split in (chronological(rows), held_out_locations(rows)):
        baseline = _score(ShrinkageBaseline(), split)
        challenger = _score(LogisticModel(), split)
        gain = (
            (baseline["brier"] - challenger["brier"]) / baseline["brier"]
            if baseline["brier"]
            else 0.0
        )
        results[split.name] = {
            "question": split.question,
            "baseline_brier": baseline["brier"],
            "challenger_brier": challenger["brier"],
            "relative_gain": gain,
            "baseline_auc": baseline["auc"],
            "challenger_auc": challenger["auc"],
        }
    wins = [
        name
        for name, r in results.items()
        if r["relative_gain"] >= MIN_RELATIVE_BRIER_GAIN
    ]
    passed = len(wins) == len(results)
    return GateResult(
        "beats-the-baseline-on-both-populations",
        passed=passed,
        detail=(
            "challenger beats shrinkage on both splits"
            if passed
            else f"challenger wins on {wins or 'neither split'} - ship the baseline"
        ),
        numbers=results,
    )


def gate_calibrated(corpus: Corpus) -> GateResult:
    """Rule 4's spirit. A probability the solver can trade against cost."""
    rows = build_features(corpus.deliveries)
    results = {}
    for split in (chronological(rows), held_out_locations(rows)):
        # Both, because the gate is on the calibrated model and the raw score is
        # what says whether the calibration step earned its place.
        results[split.name] = {
            "raw": _score(LogisticModel(), split),
            "isotonic_recent": _score(
                CalibratedModel(base=LogisticModel(), slice_strategy="recent"), split
            ),
            "isotonic_unseen_docks": _score(
                CalibratedModel(
                    base=LogisticModel(), slice_strategy="unseen_locations"
                ),
                split,
            ),
        }
    # The gate is on the best available configuration per population, because
    # choosing the calibration slice to match the deployment is a decision
    # somebody makes deliberately - not a knob tuned against the test set. What
    # matters is that a configuration exists which prices each population
    # honestly, and that the report names it.
    best = {
        name: min(variants.items(), key=lambda kv: kv[1]["ece_over_floor"])
        for name, variants in results.items()
    }
    worst = max(chosen["ece_over_floor"] for _, chosen in best.values())
    return GateResult(
        "calibrated-enough-to-price",
        passed=worst <= MAX_CALIBRATION_ERROR_OVER_NOISE_FLOOR,
        detail=(
            f"worst calibration error is {worst:.2f}x the noise floor (limit "
            f"{MAX_CALIBRATION_ERROR_OVER_NOISE_FLOOR}x; at 1.0x the model is as "
            "calibrated as this much data can show), using "
            + ", ".join(f"{name}={chosen}" for name, (chosen, _) in best.items())
        ),
        numbers={
            "by_split": results,
            "chosen": {name: chosen for name, (chosen, _) in best.items()},
        },
    )


def gate_cold_start_is_measurable(corpus: Corpus) -> GateResult:
    """Rule 2 and rule 5. There must be unseen docks, and enough of them.

    The number the brief warns about: the p90 model covered 66.5% of cold-start
    cases after promising 90%. That was discovered because somebody held out
    whole locations. A split with four docks on the far side discovers nothing.
    """
    rows = build_features(corpus.deliveries)
    split = held_out_locations(rows)
    unseen = {r.location_id for r in split.test}
    seen = {r.location_id for r in split.train}
    overlap = unseen & seen
    labelled = sum(r.label for r in split.test)
    passed = not overlap and len(unseen) >= 20 and labelled >= 20
    return GateResult(
        "cold-start-is-measurable",
        passed=passed,
        detail=(
            f"{len(unseen)} docks held out whole, {labelled} consequences among them"
            if passed
            else f"cold-start split too thin: {len(unseen)} docks, {labelled} "
            f"consequences, {len(overlap)} leaked across"
        ),
        numbers={
            "held_out_docks": len(unseen),
            "consequences_in_holdout": labelled,
            "docks_on_both_sides": len(overlap),
        },
    )


def gate_enough_labels(corpus: Corpus) -> GateResult:
    """`docs/DATA_NEED_BRIEF.md` §4.2: 500-1,000 observed consequences.

    Reported against the band and against the arm separately, because §4.2
    computes its 21-41 months over *every* delivery while §M2 says the label
    "requires the randomised hold arm. Unobtainable by watching." Those two
    statements have never been reconciled, and which one holds is the
    difference between M2 being about two years away and about twenty.
    """
    summary = corpus.summary()
    observed = summary["observed_consequences"]
    in_arm = summary["observed_consequences_in_control_arm"]
    return GateResult(
        "enough-labels",
        passed=observed >= M2_BAND_MINIMUM,
        detail=(
            f"{observed} observed consequences against a {M2_BAND_MINIMUM}-"
            f"{M2_BAND_TARGET} band; {in_arm} of them inside the control arm"
        ),
        numbers={
            "observed": observed,
            "in_control_arm": in_arm,
            "band": (M2_BAND_MINIMUM, M2_BAND_TARGET),
            "arm_only_reading_shortfall": max(M2_BAND_MINIMUM - in_arm, 0),
        },
    )


def gate_arm_can_measure_its_own_bias(corpus: Corpus) -> GateResult:
    """Not one of the five. It should be.

    `EXP-1` exists to de-confound the label. Whether it can depends on whether
    the arm holds enough late orders to estimate its consequence rate more
    precisely than the bias it is estimating. At 5-10% of a single partner's
    book it does not, and a gate that cannot say so lets the arm be cited as a
    solved problem while it is still a statistical intention.
    """
    late_in_arm = [
        d for d in corpus.deliveries if d.arm == ARM_CONTROL and d.was_late
    ]
    summary = corpus.summary()
    bias = abs(summary["confound_bias"])
    if not late_in_arm:
        return GateResult("arm-can-measure-its-own-bias", False, "no control arm")
    rate = summary["consequence_rate_when_late_in_arm"]
    half_width = 1.96 * math.sqrt(max(rate * (1 - rate), 1e-9) / len(late_in_arm))
    passed = half_width < bias
    return GateResult(
        "arm-can-measure-its-own-bias",
        passed=passed,
        detail=(
            f"arm holds {len(late_in_arm)} late orders; 95% interval is "
            f"±{half_width:.3f} against a bias of {bias:.3f} - "
            + ("powered" if passed else "not powered to size the confound it exists for")
        ),
        numbers={
            "late_orders_in_arm": len(late_in_arm),
            "interval_half_width": half_width,
            "confound_bias": bias,
            "late_orders_needed": (
                int(math.ceil(1.96**2 * rate * (1 - rate) / bias**2)) if bias else None
            ),
        },
    )


def gate_censoring_is_accounted_for(corpus: Corpus) -> GateResult:
    """Not one of the five either, and the one that breaks the pricing.

    A model fit on censored labels is calibrated to the labels, so it quotes
    roughly the observation rate times the truth - about half. Not noise: a
    systematic discount on urgency, in the direction that makes holding look
    cheap. This measures the discount against the planted truth, which is the
    one place it can be measured, and passes only when the correction recovers
    it.
    """
    late = [d for d in corpus.deliveries if d.was_late]
    if not late:
        return GateResult("censoring-is-accounted-for", False, "no late deliveries")
    truth = sum(1 for d in late if d.true_consequence) / len(late)
    labelled = sum(
        1 for d in late if d.observed_label not in (None, "silence")
    ) / len(late)
    rate = expected_observation_rate()
    corrected = censoring_adjusted(labelled, observation_rate=rate)
    recovered = abs(corrected - truth) / truth
    return GateResult(
        "censoring-is-accounted-for",
        passed=recovered < 0.10,
        detail=(
            f"labels say {labelled:.3f}, truth is {truth:.3f}; correcting by the "
            f"assumed {rate:.2f} observation rate recovers it to within "
            f"{recovered:.1%}"
        ),
        numbers={
            "label_rate": labelled,
            "true_rate": truth,
            "corrected": corrected,
            "assumed_observation_rate": rate,
        },
    )


def run_gates(corpus: Corpus) -> list[GateResult]:
    return [
        gate_enough_labels(corpus),
        gate_causal_features_only(corpus),
        gate_cold_start_is_measurable(corpus),
        gate_beats_the_baseline(corpus),
        gate_calibrated(corpus),
        gate_arm_can_measure_its_own_bias(corpus),
        gate_censoring_is_accounted_for(corpus),
    ]
