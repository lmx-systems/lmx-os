"""The corpus is a measuring instrument, so its own readings get asserted.

A generator nobody checks drifts. Somebody tunes a coefficient to make a chart
look better, every downstream number moves, and the harness keeps reporting
success against a world that quietly changed. These tests pin the properties
the corpus exists to have - the brief's rates, the planted confound, the
censoring - so that changing one is a deliberate act with a red test attached.

Several of these assert things that are *bad*: that the control arm is too
small to measure what it exists to measure, that censoring halves the
calibrated rate. Those are findings, not defects, and they are pinned for the
same reason - so that nobody fixes the symptom by adjusting the generator.
"""
import math

import pytest

from app.identity.node_class import NODE_CLASSES
from app.record.consequences import M2_BAND_MINIMUM, M2_BAND_TARGET, SILENCE
from ml.m2.corpus import (
    ARM_CONTROL,
    ARM_TREATMENT,
    CONSEQUENCE_MIX,
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    OBSERVABILITY,
    POLICY_LATE_RATE,
    TARGET_LATE_RATE,
    TRUTH_COLUMNS,
    expected_late_rate,
    expected_observation_rate,
    generate_corpus,
)
from ml.m2.population import (
    NODE_CLASS_SHARE,
    NODE_CLASS_URGENCY,
    TARGET_CONSEQUENCE_RATE_WHEN_LATE,
    lateness_response,
)


@pytest.fixture(scope="module")
def corpus():
    """One corpus at the brief's scale, reused. About a second to build."""
    return generate_corpus()


@pytest.fixture(scope="module")
def summary(corpus):
    return corpus.summary()


class TestItMatchesTheBriefsAssumptions:
    """Every rate here traces to `docs/DATA_NEED_BRIEF.md` §4.2. If one drifts,
    the corpus is no longer a corpus of the thing the sizing was computed for
    and every month-count downstream of it is wrong."""

    def test_the_late_rate_is_about_one_in_ten(self, summary):
        assert abs(summary["late_rate"] - TARGET_LATE_RATE) < 0.01

    def test_the_per_class_late_rates_average_to_that(self):
        """The confound is built by varying lateness across classes. It must
        vary around the brief's figure rather than away from it, or the corpus
        is denser or sparser in labels than the sizing assumed."""
        assert abs(expected_late_rate() - TARGET_LATE_RATE) < 0.005

    def test_about_half_of_consequences_are_ever_observed(self, summary):
        assert abs(expected_observation_rate() - 0.5) < 0.02
        assert abs(summary["observation_rate"] - 0.5) < 0.03

    def test_one_late_stop_in_six_has_a_consequence(self, corpus):
        """The only measured figure in the chain - one full manifest. The
        intercept is solved to hit it, so this checks the solver, and it is
        checked in the control arm because the treatment arm is confounded by
        construction."""
        arm_late = [d for d in corpus.deliveries if d.arm == ARM_CONTROL and d.was_late]
        rate = sum(1 for d in arm_late if d.true_consequence) / len(arm_late)
        assert abs(rate - TARGET_CONSEQUENCE_RATE_WHEN_LATE) < 0.03

    def test_the_stated_scale_produces_the_stated_labels(self, summary):
        """62,500-125,000 deliveries for 500-1,000 observed consequences. The
        default sits inside that, and the labels land inside the band."""
        assert 62_500 <= summary["deliveries"] <= 125_000
        assert M2_BAND_MINIMUM <= summary["observed_consequences"] <= M2_BAND_TARGET


class TestThePlantedConfound:
    """Why `EXP-1` exists, made into data."""

    def test_we_hold_longest_exactly_where_lateness_costs_least(self):
        """The mechanism. `MODEL_AND_DATA_BRIEF.md` §M3 measures hold value as
        highest at warehouse and transfer and lowest at shops; those are also
        the least and most consequence-prone nodes. Rank correlation must be
        strongly negative or the confound is not the one we claim to plant."""
        classes = [c for c in NODE_CLASSES if c in POLICY_LATE_RATE]
        late_rank = {c: i for i, c in enumerate(sorted(classes, key=POLICY_LATE_RATE.get))}
        urgency_rank = {
            c: i for i, c in enumerate(sorted(classes, key=NODE_CLASS_URGENCY.get))
        }
        n = len(classes)
        squared = sum((late_rank[c] - urgency_rank[c]) ** 2 for c in classes)
        spearman = 1 - (6 * squared) / (n * (n**2 - 1))
        assert spearman < -0.9, f"rank correlation is {spearman:.3f}, not strongly negative"

    def test_the_observational_rate_understates_the_truth(self, summary):
        """The direction is the dangerous one. A model fit on ordinary dispatch
        concludes lateness is cheaper than it is, which licenses holding more,
        which enriches the late population further with nodes that do not mind.
        The loop closes on itself."""
        assert summary["confound_bias"] < 0
        assert (
            summary["consequence_rate_when_late_observational"]
            < summary["consequence_rate_when_late_in_arm"]
        )

    def test_the_arm_removes_it_because_the_arm_is_random(self, corpus):
        """Control orders are dispatched as the customer would have, so our
        node-class policy is not what makes them late. Late rate must be flat
        across classes in the arm and emphatically not flat outside it."""
        def spread(arm: str) -> float:
            rates = []
            for node_class in POLICY_LATE_RATE:
                subset = [
                    d for d in corpus.deliveries
                    if d.arm == arm and d.node_class == node_class
                ]
                if len(subset) >= 200:
                    rates.append(sum(1 for d in subset if d.was_late) / len(subset))
            return max(rates) - min(rates)

        assert spread(ARM_TREATMENT) > 0.10
        assert spread(ARM_CONTROL) < 0.06


class TestWhatTheCorpusSaysWeCannotDoYet:
    """Pinned because they are conclusions, and a conclusion that can be
    erased by a coefficient change is not one."""

    def test_the_arm_is_too_small_to_measure_its_own_bias(self, corpus, summary):
        """At 8% of 78,000 deliveries the control arm holds a few hundred late
        orders. The 95% interval on its consequence rate is wider than the bias
        it exists to detect, so `EXP-1` at this volume identifies the confound
        in principle and cannot size it in practice."""
        arm_late = [d for d in corpus.deliveries if d.arm == ARM_CONTROL and d.was_late]
        rate = sum(1 for d in arm_late if d.true_consequence) / len(arm_late)
        half_width = 1.96 * math.sqrt(rate * (1 - rate) / len(arm_late))
        assert half_width > abs(summary["confound_bias"]), (
            f"interval ±{half_width:.4f} no longer exceeds bias "
            f"{abs(summary['confound_bias']):.4f} - if the arm can now size the "
            "bias, say so deliberately rather than by accident"
        )

    def test_training_on_the_arm_alone_is_not_reachable(self, summary):
        """The reading of §M2 that says only arm labels count. At 8% it yields
        two orders of magnitude fewer labels than the band needs, which is the
        arithmetic that decides whether `M2` is two years away or twenty."""
        assert summary["observed_consequences_in_control_arm"] < M2_BAND_MINIMUM / 5

    def test_censoring_does_not_add_noise_it_halves_the_answer(self, corpus):
        """The part that breaks calibration rather than accuracy. `M2`'s output
        is traded against cost in the solver, and a label set that sees half the
        consequences teaches a well-calibrated model to quote half the
        probability. Correcting it is the harness's job; `REC-2`'s one SMS is
        what shrinks it at the source."""
        late = [d for d in corpus.deliveries if d.was_late]
        truth = sum(1 for d in late if d.true_consequence) / len(late)
        labelled = sum(
            1 for d in late if d.observed_label not in (None, SILENCE)
        ) / len(late)
        assert labelled < truth
        assert abs(labelled / truth - expected_observation_rate()) < 0.07

    def test_the_invisible_consequences_are_the_expensive_ones(self):
        """A part sourced from a competitor is the consequence that ends the
        account and the one we almost never see. Any averaging over
        observability hides this."""
        assert OBSERVABILITY["competitor_sourced"] < 0.2
        assert OBSERVABILITY["reorder_gap"] < 0.2
        assert OBSERVABILITY["escalation_call"] > 0.9
        # And they are not rare, which is what makes the blind spot large.
        assert CONSEQUENCE_MIX["competitor_sourced"] + CONSEQUENCE_MIX["reorder_gap"] > 0.4


class TestTheContractWithAHarness:
    def test_the_answer_is_never_in_the_features(self):
        assert not set(FEATURE_COLUMNS) & set(TRUTH_COLUMNS)
        assert LABEL_COLUMN in TRUTH_COLUMNS

    def test_realised_lateness_is_not_a_feature(self):
        """`M2` is asked before the delivery runs. A model handed the lateness
        it actually incurred has been told the conditioning event."""
        assert "minutes_late" not in FEATURE_COLUMNS
        assert "was_late" not in FEATURE_COLUMNS

    def test_every_feature_exists_on_a_row(self, corpus):
        row = corpus.deliveries[0]
        for column in FEATURE_COLUMNS + TRUTH_COLUMNS:
            assert hasattr(row, column), column

    def test_every_row_is_stamped_synthetic(self, corpus, summary):
        assert summary["synthetic"] is True
        assert all(d.synthetic for d in corpus.deliveries[:1000])

    def test_the_same_seed_gives_the_same_corpus(self):
        a = generate_corpus(deliveries=2_000, locations=40, operating_days=60, seed=11)
        b = generate_corpus(deliveries=2_000, locations=40, operating_days=60, seed=11)
        assert [d.order_id for d in a.deliveries] == [d.order_id for d in b.deliveries]
        assert [d.observed_label for d in a.deliveries] == [
            d.observed_label for d in b.deliveries
        ]

    def test_a_different_seed_gives_a_different_one(self):
        a = generate_corpus(deliveries=2_000, locations=40, operating_days=60, seed=11)
        b = generate_corpus(deliveries=2_000, locations=40, operating_days=60, seed=12)
        assert [d.observed_label for d in a.deliveries] != [
            d.observed_label for d in b.deliveries
        ]


class TestColdStartIsInTheData:
    """`MODEL_AND_DATA_BRIEF.md` rule (5): a new dock inherits a prior from its
    node class, and that has to be a property of the corpus before it can be a
    property of a model."""

    def test_locations_are_drawn_from_class_priors_not_independently(self, corpus):
        by_class: dict[str, list[float]] = {}
        for location in corpus.locations:
            by_class.setdefault(location.node_class, []).append(location.effect)
        populous = {c: v for c, v in by_class.items() if len(v) >= 20}
        assert len(populous) >= 3
        for node_class, effects in populous.items():
            mean = sum(effects) / len(effects)
            assert abs(mean) < 0.35, f"{node_class} effects are not centred"

    def test_there_are_enough_docks_to_hold_whole_ones_out(self, corpus):
        """Rule (2) wants entire unseen locations as the cold-start split. A
        handful of docks cannot produce one."""
        assert len(corpus.locations) >= 200

    def test_the_rarest_class_is_rare(self, corpus):
        """§4.4 note 3: node-class frequency is skewed and the binding sample
        size is the rarest class. A corpus with an even spread would hide the
        problem rather than pose it."""
        counts = {c: 0 for c in NODE_CLASS_SHARE}
        for location in corpus.locations:
            counts[location.node_class] += 1
        share = min(counts.values()) / len(corpus.locations)
        assert share < 0.06


class TestTheDoseResponse:
    def test_five_minutes_late_is_not_a_tenth_of_fifty(self):
        assert lateness_response(50) < 10 * lateness_response(5)

    def test_it_saturates(self):
        """The second two hours of lateness add almost nothing on top of the
        first two. By then the customer has made whatever call they were going
        to make."""
        first = lateness_response(120) - lateness_response(0)
        second = lateness_response(240) - lateness_response(120)
        assert second < 0.1 * first

    def test_on_time_is_zero(self):
        assert lateness_response(0) == 0.0
        assert lateness_response(-5) == 0.0
