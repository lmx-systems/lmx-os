"""M1's dwell harness: causal features, honest splits, and a real guarantee.

Fixtures are invented. The property tests matter more than the numbers here -
a dwell model built on the export is about one driver's pace at 142 docks, and
`ml/real/export.py` says so in a verdict. What has to be right regardless is
that the features cannot see forward, the splits cannot overlap, and the
promise cannot be quoted without the coverage that backs it.
"""
import math
import random

import pytest

from ml.m1.baseline import (
    GlobalQuantile,
    ShrunkQuantileBaseline,
    coverage,
    pinball_loss,
    quantile,
)
from ml.m1.conformal import (
    ConformalUpperBound,
    NotEnoughCalibrationData,
    minimum_calibration_size,
)
from ml.m1.evaluate import run
from ml.m1.features import build, coldstart_split, time_split
from ml.real.export import DetailStop


def _stops(count=600, receivers=40, seed=5) -> list[DetailStop]:
    rng = random.Random(seed)
    classes = ("shop", "body_shop", "parts_store", "dealer")
    out = []
    for index in range(count):
        receiver = index % receivers
        day = 1 + index // 20
        out.append(
            DetailStop(
                receiver_id=f"R{receiver:03d}",
                route_id=f"M{day}",
                driver_id="D1",
                stop_seq=index % 20 + 1,
                arrived=__import__("datetime").datetime(2026, 5, 1)
                + __import__("datetime").timedelta(days=day, minutes=index % 400),
                # Per-receiver level plus noise, so history is worth something.
                dwell_sec=max(5.0, rng.gauss(60 + receiver * 6, 40)),
                travel_sec=rng.uniform(120, 900),
                revenue=rng.uniform(10, 300),
                pieces=rng.randint(0, 5),
                weight=0.0,
                node_class=classes[receiver % len(classes)],
            )
        )
    return out


@pytest.fixture(scope="module")
def rows():
    return build(_stops())


class TestTheFeaturesCannotSeeForward:
    def test_the_first_visit_to_a_dock_has_no_history(self, rows):
        seen = set()
        for row in sorted(rows, key=lambda r: r.arrived):
            if row.receiver_id not in seen:
                assert row.recv_prior_n == 0
                assert row.recv_prior_mean is None
                seen.add(row.receiver_id)

    def test_changing_the_last_stop_moves_nothing_before_it(self):
        """The perturbation test, same as `ml/m2/gates.py`. A score cannot police
        leakage because the leak improves every score; the property can."""
        stops = _stops()
        before = build(stops)
        last = max(range(len(stops)), key=lambda i: stops[i].arrived)
        stops[last] = DetailStop(
            **{**stops[last].__dict__, "dwell_sec": stops[last].dwell_sec * 20}
        )
        after = build(stops)
        moved = [
            a.receiver_id
            for a, b in zip(before, after)
            if a.arrived != b.arrived or a.recv_prior_mean != b.recv_prior_mean
        ][:-1]
        assert not moved

    def test_history_is_the_mean_of_what_came_before(self, rows):
        """Hand-check one dock rather than trusting the accumulator."""
        target = rows[0].receiver_id
        history = [r for r in rows if r.receiver_id == target]
        running = []
        for row in history[:20]:
            expected = sum(running) / len(running) if running else None
            if expected is None:
                assert row.recv_prior_mean is None
            else:
                assert row.recv_prior_mean == pytest.approx(expected)
            running.append(row.dwell_min)


class TestTheSplits:
    def test_the_time_split_puts_no_day_on_both_sides(self, rows):
        train, test, cut = time_split(rows)
        assert max(r.day for r in train) < cut <= min(r.day for r in test)

    def test_cold_receivers_never_appear_in_training(self, rows):
        train_all, test, _ = time_split(rows)
        train, warm, cold = coldstart_split(train_all, test)
        assert not {r.receiver_id for r in train} & {r.receiver_id for r in cold}

    def test_warm_and_cold_do_not_overlap(self, rows):
        train_all, test, _ = time_split(rows)
        _, warm, cold = coldstart_split(train_all, test)
        assert not {r.receiver_id for r in warm} & {r.receiver_id for r in cold}

    def test_the_holdout_is_stable_between_runs(self, rows):
        train_all, test, _ = time_split(rows)
        first = {r.receiver_id for r in coldstart_split(train_all, test)[2]}
        second = {r.receiver_id for r in coldstart_split(train_all, test)[2]}
        assert first == second


class TestTheShrunkBaseline:
    def test_a_dock_with_no_history_gets_its_class(self, rows):
        model = ShrunkQuantileBaseline(q=0.5).fit(rows)
        unseen = [r for r in rows if r.receiver_id == "R999"]
        assert not unseen
        fabricated = rows[0].__class__(**{**rows[0].__dict__, "receiver_id": "R999"})
        assert model.predict([fabricated])[0] == pytest.approx(
            model.class_[fabricated.node_class]
        )

    def test_a_well_observed_dock_keeps_most_of_its_own_number(self, rows):
        model = ShrunkQuantileBaseline(q=0.5).fit(rows)
        busiest = max(model.receiver_n, key=model.receiver_n.get)
        weight = model.receiver_n[busiest] / (model.receiver_n[busiest] + 10.0)
        assert weight > 0.5

    def test_it_beats_a_single_global_number(self, rows):
        """`PRD-3`: *beats a global model on held-out data*. If it does not, the
        per-dock structure carries nothing and the constant is the honest
        answer."""
        train, test, _ = time_split(rows)
        actual = [r.dwell_min for r in test]
        shrunk = pinball_loss(actual, ShrunkQuantileBaseline(q=0.5).fit(train).predict(test), 0.5)
        flat = pinball_loss(actual, GlobalQuantile(q=0.5).fit(train).predict(test), 0.5)
        assert shrunk < flat

    def test_the_quantile_matches_a_hand_computed_one(self):
        assert quantile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
        assert quantile([1, 2, 3, 4], 0.0) == 1
        assert quantile([1, 2, 3, 4], 1.0) == 4

    def test_pinball_charges_more_for_under_promising_at_p90(self):
        under = pinball_loss([10.0], [5.0], 0.9)
        over = pinball_loss([10.0], [15.0], 0.9)
        assert under == pytest.approx(9 * over)


class TestTheConformalGuarantee:
    def test_it_delivers_the_coverage_it_claims(self):
        """The property the whole module exists for, checked by simulation.

        Exchangeable draws, a deliberately badly calibrated predictor, and the
        coverage still lands at or above 90% - which is the point: the guarantee
        is distribution-free and says nothing about the model being good."""
        rng = random.Random(3)
        kept = 0
        trials = 300
        for _ in range(trials):
            draws = [rng.lognormvariate(1.0, 0.8) for _ in range(400)]
            calibration, test = draws[:300], draws[300:]
            # A predictor that is wrong on purpose.
            predicted = [2.0] * len(calibration)
            bound = ConformalUpperBound.fit(calibration, predicted, alpha=0.10)
            promised = bound.apply([2.0] * len(test))
            kept += coverage(test, promised) >= 0.80
        assert kept / trials > 0.95

    def test_average_coverage_lands_on_the_target(self):
        """Not above 0.90 on every run - that is not what the guarantee says.

        Conformal promises coverage in expectation, and an empirical mean over a
        finite number of trials scatters around it. So the assertion is against
        the target minus the standard error of the estimate, and separately
        against being wildly conservative: a bound that covered 99% when it
        promised 90% is quoting minutes nobody needed to wait.
        """
        rng = random.Random(11)
        trials, per_trial = 200, 50
        observed = []
        for _ in range(trials):
            draws = [rng.gauss(10, 3) for _ in range(200 + per_trial)]
            calibration, test = draws[:200], draws[200:]
            bound = ConformalUpperBound.fit(calibration, [9.0] * 200, alpha=0.10)
            observed.append(coverage(test, bound.apply([9.0] * len(test))))

        mean = sum(observed) / len(observed)
        standard_error = math.sqrt(0.9 * 0.1 / (trials * per_trial))
        assert mean >= 0.90 - 3 * standard_error, (
            f"coverage {mean:.4f} is more than three standard errors "
            f"({standard_error:.4f}) below the 90% it guarantees"
        )
        assert mean < 0.95, f"coverage {mean:.4f} is far above target - over-wide"

    def test_it_refuses_a_calibration_set_too_small_to_promise(self):
        """Nine residuals is the floor for 90%. Below it no finite bound carries
        the guarantee, and clamping silently would quote one that was not
        earned."""
        assert minimum_calibration_size(0.10) == 9
        assert minimum_calibration_size(0.01) == 99
        with pytest.raises(NotEnoughCalibrationData):
            ConformalUpperBound.fit([1.0] * 5, [1.0] * 5, alpha=0.10)

    def test_it_tightens_an_over_generous_prediction(self):
        """The adjustment is signed. A p90 that already covers 98% should come
        down, not stay where it is - over-promising costs money too."""
        bound = ConformalUpperBound.fit([1.0] * 100, [50.0] * 100, alpha=0.10)
        assert bound.adjustment < 0


class TestTheEvaluationReportsBothPopulations:
    def test_it_scores_warm_and_cold_separately(self, rows):
        evaluation = run(rows)
        populations = {s.population for s in evaluation.scores}
        assert any(p.startswith("warm") for p in populations)
        assert any(p.startswith("cold") for p in populations)

    def test_every_promise_carries_the_population_it_was_calibrated_on(self, rows):
        for promise in run(rows).promises:
            assert promise.calibrated_on
            assert 0.0 <= promise.conformal_coverage <= 1.0

    def test_the_coverage_floor_allows_for_sample_size(self, rows):
        """A hard `>= 0.90` would fail a correct bound that landed on 0.898
        across 254 stops. The floor is the target minus binomial noise."""
        for promise in run(rows).promises:
            expected = 0.9 - 1.96 * math.sqrt(0.9 * 0.1 / promise.n)
            assert promise.coverage_floor == pytest.approx(expected, abs=1e-4)
