"""PRD-5: the model that has to beat the lookup table, or not ship.

Skipped wherever scikit-learn is absent, which is CI and the application venv.
That is the design rather than a compromise: `ml/m1/`'s harness, baseline and
conformal bound are standard library so the release gate keeps running where no
ML stack is installed, and a gate that only runs sometimes is not a gate.
"""
import random
from datetime import datetime, timedelta

import pytest

from ml.m1.challenger import (
    BLOCKED_REASON,
    INTENDED_LIBRARY,
    feature_vector,
    is_available,
)
from ml.m1.evaluate import Comparison, Evaluation, paired_difference, run
from ml.m1.features import build, shrunk_history
from ml.real.export import DetailStop

needs_sklearn = pytest.mark.skipif(
    not is_available(), reason="scikit-learn is not installed in this interpreter"
)


def _stops(count=700, receivers=40, seed=5) -> list[DetailStop]:
    rng = random.Random(seed)
    classes = ("shop", "body_shop", "parts_store", "dealer")
    out = []
    for index in range(count):
        receiver = index % receivers
        day = 1 + index // 20
        out.append(
            DetailStop(
                receiver_id=f"R{receiver:03d}", route_id=f"M{day}", driver_id="D1",
                stop_seq=index % 20 + 1,
                arrived=datetime(2026, 5, 1) + timedelta(days=day, minutes=index % 400),
                dwell_sec=max(5.0, rng.gauss(60 + receiver * 6, 40)),
                travel_sec=rng.uniform(120, 900), revenue=rng.uniform(10, 300),
                pieces=rng.randint(0, 5), weight=0.0,
                node_class=classes[receiver % len(classes)],
            )
        )
    return out


class TestTheFeatureVector:
    def test_the_dock_id_is_absent_on_purpose(self):
        """scikit-learn has no native categorical handling, and one-hot
        encoding 142 docks onto ~1,400 rows hands the model a column per dock
        with ten observations behind it. The dock enters through its history."""
        rows = build(_stops())
        vector = feature_vector(rows[-1])
        assert len(vector) == len(feature_vector(rows[0]))
        assert all(isinstance(value, float) for value in vector)

    def test_a_cold_dock_is_a_regime_not_a_missing_number(self):
        """A tree can only split on 'never seen before' if it is told. Encoding
        it as -1 alone would have the model interpolate between 'no history' and
        'a very short prior dwell'."""
        rows = build(_stops())
        cold = next(r for r in rows if r.recv_prior_n == 0)
        warm = next(r for r in rows if r.recv_prior_n > 5)
        assert feature_vector(cold)[-len(_NODE_CLASSES()) - 1] == 1.0
        assert feature_vector(warm)[-len(_NODE_CLASSES()) - 1] == 0.0

    def test_a_thin_docks_history_is_shrunk_before_the_tree_sees_it(self):
        """The brief's gate 3. Raw, one visit's dwell spoke as loudly as a
        hundred's; the vector carries the history pulled toward the dock's
        location type, with the visit count alongside so trust can be learned."""
        rows = build(_stops())
        row = next(
            r for r in rows
            if r.recv_prior_n == 1
            and r.class_prior_mean is not None
            and abs(r.recv_prior_mean - r.class_prior_mean) > 0.5
        )
        mean, p90 = shrunk_history(row, 10.0)
        vector = feature_vector(row, prior_strength=10.0)
        assert vector[11] == pytest.approx(mean)
        assert vector[12] == pytest.approx(p90)
        assert vector[11] != pytest.approx(row.recv_prior_mean)
        assert vector[10] == 1.0

    def test_missing_history_is_not_silently_zero(self):
        """Zero minutes of prior dwell and no prior dwell are different facts,
        and a tree cannot tell them apart if both arrive as 0.0."""
        rows = build(_stops())
        cold = next(r for r in rows if r.recv_prior_mean is None)
        assert -1.0 in feature_vector(cold)


def _NODE_CLASSES():
    from app.identity.node_class import NODE_CLASSES

    return NODE_CLASSES


class TestTheIntendedLibraryIsRecorded:
    def test_it_names_what_the_brief_asked_for(self):
        """The brief specifies LightGBM, chosen over XGBoost because
        `receiver_id` is high-cardinality categorical. Substituting quietly
        would make a loss here read as a verdict on that choice."""
        assert INTENDED_LIBRARY == "lightgbm"
        assert "libomp" in BLOCKED_REASON


@needs_sklearn
class TestTheChallenger:
    def test_it_learns_something(self):
        from ml.m1.challenger import GradientBoostedQuantile
        from ml.m1.features import time_split

        rows = build(_stops())
        train, test, _ = time_split(rows)
        predictions = GradientBoostedQuantile(q=0.5).fit(train).predict(test)
        assert max(predictions) - min(predictions) > 0.1

    def test_it_never_promises_a_negative_dwell(self):
        """A quantile model is free to predict one, and the number that gets
        scored should be the number that would be promised."""
        from ml.m1.challenger import GradientBoostedQuantile

        rows = build(_stops())
        assert all(p >= 0 for p in GradientBoostedQuantile(q=0.1).fit(rows).predict(rows))

    def test_an_empty_fit_does_not_explode(self):
        from ml.m1.challenger import GradientBoostedQuantile

        model = GradientBoostedQuantile().fit([])
        assert model.predict([]) == []


class TestTheGate:
    def test_without_a_challenger_nothing_ships(self):
        """An empty loss list with no challenger scored is not a pass - nothing
        was tested. A gate that reads green when it did not run is worse than no
        gate."""
        evaluation = run(_stops())
        ships, reasons = evaluation.challenger_verdict()
        assert ships is False
        assert reasons == ["no challenger was scored"]

    def test_a_win_the_data_cannot_show_does_not_ship(self):
        """Decided 30 September 2026: beating the baseline means an advantage
        the data can show - the paired interval clear of zero - on every
        population. On the real export, three of the challenger's four point
        wins were not."""
        evaluation = Evaluation(cut_day="2026-06-01", comparisons=[
            Comparison("warm/p50", -0.03, (-0.20, 0.15)),
            Comparison("cold/p90", -0.40, (-0.60, -0.20)),
        ])
        assert evaluation.challenger_verdict() == (False, ["warm/p50"])

    def test_a_win_shown_everywhere_ships(self):
        evaluation = Evaluation(cut_day="2026-06-01", comparisons=[
            Comparison("warm/p50", -0.10, (-0.20, -0.01)),
            Comparison("cold/p90", -0.40, (-0.60, -0.20)),
        ])
        assert evaluation.challenger_verdict() == (True, [])

    def test_a_tie_or_a_missing_interval_is_not_a_win(self):
        assert not Comparison("warm/p90", -0.05, (-0.10, 0.0)).shown
        assert not Comparison("warm/p90", -0.05, None).shown

    @needs_sklearn
    def test_every_population_gets_a_paired_comparison(self):
        evaluation = run(_stops(), with_challenger=True)
        assert {c.population for c in evaluation.comparisons} == {
            "warm/p50", "warm/p90", "cold/p50", "cold/p90"
        }

    def test_the_harness_still_runs_with_no_ml_stack(self):
        """The reason the rest of ml/m1/ is standard library."""
        evaluation = run(_stops(), with_challenger=False)
        assert evaluation.scores
        assert any(s.model == "shrunk-quantile" for s in evaluation.scores)

    @needs_sklearn
    def test_it_judges_every_population_not_the_average(self):
        """Rule (3) exists because the p90 model covered 66.5% of cold-start
        cases after promising 90%. An average across warm and cold hides it."""
        evaluation = run(_stops(), with_challenger=True)
        judged = {
            s.population for s in evaluation.scores if s.model.startswith("sklearn-")
        }
        assert judged == {"warm/p50", "warm/p90", "cold/p50", "cold/p90"}

    @needs_sklearn
    def test_losing_one_population_blocks_the_release(self):
        evaluation = run(_stops(), with_challenger=True)
        ships, lost_on = evaluation.challenger_verdict()
        assert ships is (lost_on == [])
        assert any("PRD-5" in note for note in evaluation.notes)

    def test_an_unavailable_stack_is_reported_not_silent(self):
        """Asking for a challenger and getting none must leave a trace. A run
        that quietly scored only the baseline would read as a clean gate."""
        if is_available():
            pytest.skip("scikit-learn is installed here, so nothing is blocked")
        evaluation = run(_stops(), with_challenger=True)
        assert any("no challenger was scored" in note for note in evaluation.notes)



class TestThePairedDifference:
    """Challenger minus baseline, dock by dock. Whole docks are resampled, so
    a win that lives at one dock is not mistaken for a win."""

    def test_identical_predictions_differ_by_nothing(self):
        rows = build(_stops())
        same = [1.0] * len(rows)
        assert paired_difference(rows, same, same, 0.5, seed="t") == (0.0, (0.0, 0.0))

    def test_better_at_every_dock_is_shown(self):
        rows = build(_stops())
        truth = [r.dwell_min for r in rows]
        difference, interval = paired_difference(
            rows, truth, [t + 2.0 for t in truth], 0.5, seed="t"
        )
        assert difference < 0
        assert Comparison("x", difference, interval).shown

    def test_better_at_one_dock_only_is_not_shown(self):
        """The resamples that leave that dock out find no difference at all."""
        rows = build(_stops())
        target = rows[0].receiver_id
        truth = [r.dwell_min for r in rows]
        challenger = [t if r.receiver_id == target else t + 1.0 for r, t in zip(rows, truth)]
        baseline = [t + 3.0 if r.receiver_id == target else t + 1.0 for r, t in zip(rows, truth)]
        difference, interval = paired_difference(rows, challenger, baseline, 0.5, seed="t")
        assert difference < 0
        assert not Comparison("x", difference, interval).shown
