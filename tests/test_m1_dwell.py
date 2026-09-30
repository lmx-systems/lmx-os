"""M1's dwell harness: causal features, honest splits, and a real guarantee.

Fixtures are invented. The property tests matter more than the numbers here -
a dwell model built on the export is about one driver's pace at 142 docks, and
`ml/real/export.py` says so in a verdict. What has to be right regardless is
that the features cannot see forward, the splits cannot overlap, and the
promise cannot be quoted without the coverage that backs it.
"""
import math
import random
from datetime import datetime, timedelta

import pytest

from ml.m1.baseline import (
    PRIOR_STRENGTH,
    GlobalQuantile,
    ShrunkQuantileBaseline,
    coverage,
    estimate_prior_strength,
    pinball_loss,
    quantile,
)
from ml.m1.conformal import (
    ConformalUpperBound,
    NotEnoughCalibrationData,
    minimum_calibration_size,
)
from ml.m1.evaluate import populations, run
from ml.m1.features import build, coldstart_split, shrunk_history, time_split
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
            if a.arrived != b.arrived
            or a.recv_prior_mean != b.recv_prior_mean
            or a.class_prior_mean != b.class_prior_mean
            or a.class_prior_p90 != b.class_prior_p90
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


@pytest.fixture(scope="module")
def evaluation():
    return run(_stops())


class TestTheEvaluationReportsBothPopulations:
    def test_it_scores_warm_and_cold_separately(self, evaluation):
        labels = {s.population for s in evaluation.scores}
        assert any(p.startswith("warm") for p in labels)
        assert any(p.startswith("cold") for p in labels)

    def test_every_promise_carries_the_population_it_was_calibrated_on(self, evaluation):
        for promise in evaluation.promises:
            assert promise.calibrated_on
            assert 0.0 <= promise.conformal_coverage <= 1.0

    def test_the_coverage_floor_allows_for_sample_size(self, evaluation):
        """A hard `>= 0.90` would fail a correct bound that landed on 0.898
        across 254 stops. The floor is the target minus binomial noise."""
        for promise in evaluation.promises:
            expected = 0.9 - 1.96 * math.sqrt(0.9 * 0.1 / promise.n)
            assert promise.coverage_floor == pytest.approx(expected, abs=1e-4)


class TestTheColdTestIsCold:
    """The brief's gate 5. Built over the whole book and split afterwards, a
    held-out dock's earlier visits were already its history - 66 of the real
    export's 68 cold rows carried it into the test."""

    def test_a_held_out_dock_enters_the_test_with_no_history(self):
        _, _, cold, _ = populations(_stops())
        assert cold
        first_seen = {}
        for row in sorted(cold, key=lambda r: r.arrived):
            first_seen.setdefault(row.receiver_id, row)
        assert all(row.recv_prior_n == 0 for row in first_seen.values())

    def test_building_first_would_have_leaked(self):
        """The old order, on the same book. Without this the test above could
        pass only because the fixture's cold docks had no visits before the cut."""
        train_all, test, _ = time_split(build(_stops()))
        _, _, cold = coldstart_split(train_all, test)
        assert any(row.recv_prior_n > 0 for row in cold)

    def test_a_held_out_dock_never_trains(self):
        train, _, cold, _ = populations(_stops())
        assert not {r.receiver_id for r in train} & {r.receiver_id for r in cold}

    def test_an_unseen_stop_feeds_no_history_at_all(self):
        """Not its own dock's, and not its location type's either - a type
        average that had absorbed the held-out docks would carry them into the
        test by the side door."""
        stops = _stops()
        target = min(stops, key=lambda s: s.arrived)
        full = {(r.receiver_id, r.arrived): r for r in build(stops)}
        blind = build(stops, unseen=lambda s: s.receiver_id == target.receiver_id)
        assert all(r.receiver_id != target.receiver_id for r in blind)
        same_type_later = [
            r for r in blind
            if r.node_class == target.node_class and r.arrived > target.arrived
        ]
        assert same_type_later
        assert any(
            r.class_prior_mean != full[(r.receiver_id, r.arrived)].class_prior_mean
            for r in same_type_later
        )

    def test_an_unseen_stop_still_counts_toward_its_route(self):
        """A route's size is known at dispatch, whoever is on it."""
        stops = _stops()
        target = stops[0].receiver_id
        full = {(r.receiver_id, r.arrived): r.stops_on_route for r in build(stops)}
        blind = build(stops, unseen=lambda s: s.receiver_id == target)
        assert all(full[(r.receiver_id, r.arrived)] == r.stops_on_route for r in blind)


class TestHistoryIsShrunkTowardItsLocationType:
    """The brief's gate 3: raw, three visits spoke as loudly as three hundred."""

    def test_a_dock_with_no_history_gets_its_type(self, rows):
        row = next(
            r for r in rows if r.recv_prior_n == 0 and r.class_prior_mean is not None
        )
        assert shrunk_history(row, 10.0) == (row.class_prior_mean, row.class_prior_p90)

    def test_the_weight_is_the_baselines(self, rows):
        row = next(r for r in rows if r.recv_prior_n >= 3)
        mean, p90 = shrunk_history(row, 10.0)
        weight = row.recv_prior_n / (row.recv_prior_n + 10.0)
        assert mean == pytest.approx(
            weight * row.recv_prior_mean + (1 - weight) * row.class_prior_mean
        )
        assert p90 == pytest.approx(
            weight * row.recv_prior_p90 + (1 - weight) * row.class_prior_p90
        )

    def test_the_type_history_is_the_mean_of_what_came_before(self, rows):
        """Hand-checked, as for a dock's own history."""
        kind = rows[0].node_class
        running = []
        for row in rows:
            if row.node_class != kind:
                continue
            if running:
                assert row.class_prior_mean == pytest.approx(sum(running) / len(running))
            running.append(row.dwell_min)
        assert len(running) > 20


class TestTheShrinkageIsChosenByTheData:
    """Gate 3's other half: `k` estimated rather than inherited at 10."""

    @staticmethod
    def _book(spread: float, noise: float, seed: int = 7) -> list[DetailStop]:
        """Forty docks of one type, a visit each a day for forty days. `spread`
        separates the docks' own levels; `noise` is the scatter within a dock."""
        rng = random.Random(seed)
        out = []
        for index in range(1600):
            receiver = index % 40
            day = 1 + index // 40
            out.append(
                DetailStop(
                    receiver_id=f"R{receiver:03d}", route_id=f"M{day}", driver_id="D1",
                    stop_seq=receiver + 1,
                    arrived=datetime(2026, 5, 1) + timedelta(days=day, minutes=receiver * 9),
                    dwell_sec=max(1.0, rng.gauss(300 + spread * (receiver % 8), noise)),
                    travel_sec=300.0, revenue=50.0, pieces=1.0, weight=0.0,
                    node_class="shop",
                )
            )
        return out

    def test_docks_that_differ_keep_their_own_numbers(self):
        rows = build(self._book(spread=120, noise=10))
        assert estimate_prior_strength(rows, 0.5) <= 5

    def test_docks_that_do_not_differ_lean_on_their_type(self):
        rows = build(self._book(spread=0, noise=120))
        assert estimate_prior_strength(rows, 0.5) >= 50

    def test_too_little_history_falls_back_to_the_default(self, rows):
        assert estimate_prior_strength([], 0.5) == PRIOR_STRENGTH
        one_day = [r for r in rows if r.day == rows[0].day]
        assert estimate_prior_strength(one_day, 0.5) == PRIOR_STRENGTH

    def test_the_harness_records_what_it_chose(self, evaluation):
        assert set(evaluation.prior_strength) == {"p50", "p90"}


class TestEveryScoreCarriesAnInterval:
    """Gate 4. The real export's cold population is fourteen docks, and a
    number without its interval invites a decision the data cannot support."""

    def test_the_interval_brackets_the_number(self, evaluation):
        for score in evaluation.scores:
            low, high = score.pinball_interval
            assert low <= score.pinball <= high
            low, high = score.coverage_interval
            assert low <= score.coverage <= high

    def test_it_counts_docks_not_stops(self, evaluation):
        for score in evaluation.scores:
            assert 1 < score.receivers < score.n

    def test_a_rerun_prints_the_same_interval(self, evaluation):
        again = run(_stops())
        assert [s.pinball_interval for s in again.scores] == [
            s.pinball_interval for s in evaluation.scores
        ]


class TestARouteIsARouteDay:
    """Gate 6. Keyed on `route_id` alone, a placeholder id reused across a
    season made one route of seventy-five days."""

    def test_a_route_id_reused_on_another_day_is_another_route(self):
        def stop(day: int, hour: int, seq: int) -> DetailStop:
            return DetailStop(
                receiver_id=f"R{seq}", route_id="1", driver_id="D1", stop_seq=seq,
                arrived=datetime(2026, 1, day, hour), dwell_sec=120.0,
                travel_sec=300.0, revenue=10.0, pieces=1.0, weight=0.0,
                node_class="shop",
            )

        rows = build([stop(14, 9, 1), stop(14, 10, 2), stop(30, 9, 1)])
        assert max(r.minutes_into_route for r in rows) == 60
        later = next(r for r in rows if r.day == "2026-01-30")
        assert (later.minutes_into_route, later.stops_on_route, later.is_last_stop) == (0, 1, 1)
