"""The harness, checked against things whose answer is known independently.

A training harness is the one piece of software whose bugs make the output look
better. Nothing downstream complains, no exception is raised, and the number
that reaches a slide is simply wrong in the flattering direction. So each piece
here is tested against an answer derived some other way - a hand-computed
expanding mean, a monotonicity property, a perturbation - rather than against
whatever it produced last time.
"""
import math

import pytest

from ml.m2.calibration import (
    IsotonicCalibrator,
    auc,
    brier_score,
    calibration_noise_floor,
    censoring_adjusted,
    expected_calibration_error,
    isotonic_fit,
    reliability,
)
from ml.m2.corpus import generate_corpus
from ml.m2.features import PRIOR_STRENGTH, build_features, leaky_location_rate
from ml.m2.gates import (
    gate_causal_features_only,
    gate_cold_start_is_measurable,
    leakage_demonstration,
    run_gates,
)
from ml.m2.models import CalibratedModel, LogisticModel, ShrinkageBaseline
from ml.m2.splits import chronological, control_arm_only, held_out_locations


@pytest.fixture(scope="module")
def small():
    """Enough to be a book, small enough to fit models in a test run."""
    return generate_corpus(deliveries=12_000, locations=90, operating_days=200, seed=99)


@pytest.fixture(scope="module")
def rows(small):
    return build_features(small.deliveries)


class TestTheFeaturesOnlyKnowThePast:
    def test_the_first_time_we_see_a_dock_it_has_no_history(self, rows):
        seen = set()
        for row in sorted(rows, key=lambda r: (r.delivered_on, r.order_id)):
            if row.location_id not in seen:
                assert row.location_late_so_far == 0
                seen.add(row.location_id)

    def test_a_dock_with_no_history_is_given_its_class_rate(self, rows):
        """Rule (5). With zero observations the shrinkage weight is zero, so the
        answer is entirely the node-class prior - which is what a new customer's
        every dock looks like in week one."""
        cold = [r for r in rows if r.location_late_so_far == 0]
        assert cold, "no cold rows in the corpus"
        for row in cold[:200]:
            assert row.shrunk_location_rate == pytest.approx(row.node_class_rate)

    def test_shrinkage_uses_the_weight_the_baseline_uses(self, rows):
        """Hand-compute the weight for a dock with history and check it. The
        constant is shared with the repo's existing shrinkage baseline so the
        two are comparable; `DATA_NEED_BRIEF.md` §4.1 flags it as contested."""
        warm = [r for r in rows if r.location_late_so_far > 5]
        assert warm
        row = warm[0]
        weight = row.location_late_so_far / (row.location_late_so_far + PRIOR_STRENGTH)
        implied = (row.shrunk_location_rate - (1 - weight) * row.node_class_rate) / weight
        assert -0.01 <= implied <= 1.01

    def test_the_gate_catches_a_builder_that_looks_forward(self, small, monkeypatch):
        """The meta-test. A gate that cannot fail is decoration, so this
        sabotages the feature builder with the exact mistake rule (1) names and
        asserts the gate goes red."""
        import ml.m2.gates as gates

        honest = build_features

        def leaky(deliveries):
            whole_file = leaky_location_rate(deliveries)
            return [
                type(r)(
                    **{**r.__dict__, "shrunk_location_rate": whole_file.get(r.location_id, 0.0)}
                )
                for r in honest(deliveries)
            ]

        assert gate_causal_features_only(small).passed is True
        monkeypatch.setattr(gates, "build_features", leaky)
        assert gate_causal_features_only(small).passed is False

    def test_no_offline_score_catches_the_leak(self, small):
        """The uncomfortable half of rule (1): the leaky model wins on the
        held-out future *and* on unseen docks, because a whole-file aggregate is
        contaminated on every side of every split drawn inside that file. This
        is why the gate is structural and not a score."""
        demo = leakage_demonstration(small)
        for split, numbers in demo.items():
            assert numbers["leaky_on_test"] < numbers["clean_on_test"], (
                f"{split}: the leak failed to flatter the test score, which would "
                "make this test's premise wrong rather than the code"
            )


class TestTheSplits:
    def test_no_day_straddles_the_chronological_cut(self, rows):
        split = chronological(rows)
        assert max(r.delivered_on for r in split.train) < min(
            r.delivered_on for r in split.test
        )

    def test_held_out_docks_appear_on_exactly_one_side(self, rows):
        split = held_out_locations(rows)
        assert not (
            {r.location_id for r in split.train} & {r.location_id for r in split.test}
        )

    def test_the_holdout_is_stable_across_runs(self, rows):
        first = {r.location_id for r in held_out_locations(rows).test}
        second = {r.location_id for r in held_out_locations(rows).test}
        assert first == second

    def test_the_arm_slice_is_only_the_arm(self, rows):
        assert {r.arm for r in control_arm_only(rows)} <= {"control"}

    def test_every_split_carries_the_question_it_answers(self, rows):
        for split in (chronological(rows), held_out_locations(rows)):
            assert split.question.endswith("?")


class TestIsotonic:
    def test_it_is_monotone(self):
        scores = [0.9, 0.1, 0.5, 0.3, 0.7, 0.2, 0.8, 0.4]
        labels = [1, 0, 1, 0, 1, 0, 0, 1]
        _, values = isotonic_fit(scores, labels)
        assert values == sorted(values)

    def test_it_recovers_a_known_miscalibration(self):
        """Scores that are exactly double the truth should be halved back."""
        scores, labels = [], []
        for i in range(2000):
            truth = (i % 20) / 100.0  # 0.00 .. 0.19
            scores.append(min(truth * 2, 1.0))
            labels.append(1 if (i * 7919) % 1000 < truth * 1000 else 0)
        calibrator = IsotonicCalibrator.fit(scores, labels)
        for truth in (0.05, 0.10, 0.15):
            assert calibrator.transform(truth * 2) == pytest.approx(truth, abs=0.05)

    def test_a_perfect_ranking_of_all_positives_stays_ordered(self):
        calibrator = IsotonicCalibrator.fit([0.1, 0.2, 0.3, 0.4], [0, 0, 1, 1])
        assert calibrator.transform(0.1) <= calibrator.transform(0.4)

    def test_an_empty_fit_does_not_explode(self):
        assert IsotonicCalibrator.fit([], []).transform(0.5) == 0.0


class TestTheMetrics:
    def test_brier_rewards_the_truth(self):
        assert brier_score([0.2] * 100, [1] * 20 + [0] * 80) < brier_score(
            [0.5] * 100, [1] * 20 + [0] * 80
        )

    def test_auc_is_half_for_a_constant_prediction(self):
        assert auc([0.3] * 50, [1, 0] * 25) == pytest.approx(0.5)

    def test_auc_is_one_for_a_perfect_ranking(self):
        assert auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == pytest.approx(1.0)

    def test_reliability_bins_by_count_not_by_width(self):
        predictions = [0.01] * 90 + [0.9] * 10
        table = reliability(predictions, [0] * 90 + [1] * 10, bins=10)
        assert len({row["n"] for row in table}) == 1

    def test_a_calibrated_model_has_low_calibration_error(self):
        predictions = [0.1] * 1000
        labels = [1 if i % 10 == 0 else 0 for i in range(1000)]
        assert expected_calibration_error(predictions, labels) < 0.02

    def test_the_noise_floor_shrinks_as_data_grows(self):
        """The property that makes it usable as a gate: more data, a stricter
        standard. A fixed threshold does the opposite."""
        def floor(n: int) -> float:
            labels = [1 if i % 15 == 0 else 0 for i in range(n)]
            return calibration_noise_floor([0.0667] * n, labels)

        assert floor(400) > floor(4_000) > floor(40_000)

    def test_the_floor_falls_roughly_as_one_over_root_n(self):
        def floor(n: int) -> float:
            labels = [1 if i % 15 == 0 else 0 for i in range(n)]
            return calibration_noise_floor([0.0667] * n, labels)

        assert floor(1_000) / floor(10_000) == pytest.approx(math.sqrt(10), rel=0.15)

    def test_the_censoring_correction_is_one_division(self):
        assert censoring_adjusted(0.07, observation_rate=0.5) == pytest.approx(0.14)

    def test_it_cannot_produce_a_probability_above_one(self):
        assert censoring_adjusted(0.8, observation_rate=0.5) == 1.0

    def test_it_refuses_an_impossible_rate(self):
        with pytest.raises(ValueError):
            censoring_adjusted(0.07, observation_rate=0.0)


class TestTheModels:
    def test_the_baseline_needs_no_training(self, rows):
        split = chronological(rows)
        untrained = ShrinkageBaseline().predict(split.test)
        trained = ShrinkageBaseline().fit(split.train).predict(split.test)
        assert untrained == trained

    def test_the_challenger_learns_something(self, rows):
        """Not that it wins - rule (3) allows it to lose. Only that it is doing
        arithmetic rather than returning the base rate."""
        split = chronological(rows)
        predictions = LogisticModel().fit(split.train).predict(split.test)
        assert max(predictions) - min(predictions) > 0.02
        assert all(0.0 <= p <= 1.0 for p in predictions)

    def test_calibration_is_never_fitted_on_the_test_rows(self, rows):
        """The rule-(1) mistake wearing a different hat. If the calibrator saw
        the test set, every model would look perfectly calibrated."""
        split = chronological(rows)
        model = CalibratedModel(base=LogisticModel()).fit(split.train)
        fitted_through = model._slice(split.train)[1]
        assert not {r.order_id for r in fitted_through} & {
            r.order_id for r in split.test
        }

    def test_the_unseen_dock_strategy_calibrates_on_docks_it_did_not_fit_on(self, rows):
        split = held_out_locations(rows)
        model = CalibratedModel(base=LogisticModel(), slice_strategy="unseen_locations")
        fit_rows, calibration_rows = model._slice(split.train)
        assert not {r.location_id for r in fit_rows} & {
            r.location_id for r in calibration_rows
        }

    def test_calibration_keeps_the_ordering(self, rows):
        """Isotonic is monotone, so it may change what a model charges and must
        never change which of two orders it ranks first.

        Compared against the calibrated model's *own* base, not a separately
        fitted one: `CalibratedModel` holds back a slice, so its base is trained
        on less data and its raw scores are legitimately a different ordering
        from a model fitted on all of train."""
        split = chronological(rows)
        model = CalibratedModel(base=LogisticModel()).fit(split.train)
        sample = split.test[:400]
        raw = model.base.predict(sample)
        calibrated = model.predict(sample)
        for i in range(len(raw)):
            for j in range(i + 1, len(raw)):
                if raw[i] < raw[j]:
                    assert calibrated[i] <= calibrated[j]

    def test_the_transform_is_monotone_across_its_whole_range(self):
        """Directly, rather than through a model - the property the ordering
        guarantee rests on."""
        calibrator = IsotonicCalibrator.fit(
            [i / 500 for i in range(500)],
            [1 if (i * 37) % 100 < i / 5 else 0 for i in range(500)],
        )
        swept = [calibrator.transform(i / 1000) for i in range(1001)]
        assert swept == sorted(swept)


class TestTheGatesReportRatherThanFlatter:
    def test_cold_start_needs_real_docks_on_the_far_side(self):
        big = generate_corpus(deliveries=40_000, locations=200, operating_days=300, seed=4)
        assert gate_cold_start_is_measurable(big).passed

    def test_a_corpus_with_four_docks_fails_the_cold_start_gate(self):
        thin = generate_corpus(deliveries=900, locations=4, operating_days=40, seed=2)
        assert gate_cold_start_is_measurable(thin).passed is False

    def test_twelve_thousand_deliveries_is_still_too_thin_for_cold_start(self, small):
        """Not a failure of the gate - the answer. Ninety docks, twenty held
        out, sixteen consequences among them: a cold-start number computed on
        that would be a number about nothing. The brief's 66.5%-against-a-
        promised-90% was discovered on a split with enough docks to discover
        it."""
        result = gate_cold_start_is_measurable(small)
        assert result.passed is False
        assert result.numbers["docks_on_both_sides"] == 0
        assert result.numbers["consequences_in_holdout"] < 20

    def test_the_gates_are_allowed_to_fail(self, small):
        """The one that matters. A harness where every gate passes on every
        input is a harness that measures nothing, and the brief is explicit that
        the challenger losing to the shrinkage baseline is an expected outcome
        with a defined consequence - ship the baseline."""
        results = run_gates(small)
        assert len(results) == 7
        assert any(not r.passed for r in results), (
            "no gate failed on a 12,000-delivery corpus, which is too small for "
            "the arm to be powered - if they now all pass, one of them stopped "
            "measuring"
        )

    def test_every_gate_explains_itself(self, small):
        for result in run_gates(small):
            assert result.detail
            assert result.name
