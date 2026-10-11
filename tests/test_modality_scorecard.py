"""DE-11: the scorecard's arithmetic and its exclusions, on invented records."""
import pytest

from ml.modality.scorecard import clopper_pearson_lower, render_markdown, scorecard


def _record(stop_id, *, site="site-t", tier="gold", first=None, second=None, group=("body_shop", "suburb")):
    labels = []
    for machine, verdict in (first or {}).items():
        labels.append({"machine": machine, "verdict": verdict, "is_blind_second": False})
    for machine, verdict in (second or {}).items():
        labels.append({"machine": machine, "verdict": verdict, "is_blind_second": True})
    return {"stop_id": stop_id, "location_id": site, "tier": tier, "group": list(group), "labels": labels}


def test_the_lower_bound_is_the_designs_arithmetic():
    # "149 unseen sites with no mistakes" is what proves 98% with 95% confidence.
    assert clopper_pearson_lower(149, 149) == pytest.approx(0.05 ** (1 / 149), abs=1e-6)
    assert clopper_pearson_lower(149, 149) > 0.98 > clopper_pearson_lower(148, 148)
    assert clopper_pearson_lower(0, 10) == 0.0
    assert clopper_pearson_lower(0, 0) is None


def test_a_scorers_cant_tell_counts_against_a_yes():
    records = [
        _record("a", first={"drone": "yes"}),
        _record("b", first={"drone": "cant_tell"}),
        _record("c", first={"drone": "no"}),
        _record("d", first={"drone": "no"}),
    ]
    predictions = {"a": {"drone": "yes"}, "b": {"drone": "yes"}, "c": {"drone": "no"}, "d": {"drone": "cant_tell"}}

    card = scorecard(records, predictions, frozenset({"site-t"}), fallback_cost=40.0)

    cell = card["cells"][0]
    assert (cell["called_yes"], cell["right_yes"], cell["wrong_yes"]) == (2, 1, 1)
    assert cell["yes_accuracy"] == 0.5
    assert cell["coverage"] == 0.75
    # One wrong yes at $40 over two orders routed to the drone.
    assert cell["wrong_yes_cost_per_routed_order"] == 20.0


def test_only_gold_records_at_test_sites_with_a_first_label_are_scored():
    records = [
        _record("scored", first={"car": "yes"}),
        _record("training-site", site="site-x", first={"car": "yes"}),
        _record("silver", tier="silver", first={"car": "yes"}),
        _record("unlabelled"),
    ]
    predictions = {r["stop_id"]: {"car": "yes"} for r in records}

    card = scorecard(records, predictions, frozenset({"site-t"}), fallback_cost=0.0)

    assert card["excluded"] == {"not_a_test_site": 1, "not_gold": 1, "no_first_label": 1}
    assert card["cells"][0]["scored"] == 1


def test_lift_is_against_the_baseline_on_the_same_records():
    records = [_record(s, first={"drone": t}) for s, t in (("a", "yes"), ("b", "no"), ("c", "yes"))]
    model = {"a": {"drone": "yes"}, "b": {"drone": "no"}, "c": {"drone": "yes"}}
    checklist = {"a": {"drone": "yes"}, "b": {"drone": "yes"}, "c": {"drone": "cant_tell"}}

    card = scorecard(records, model, frozenset({"site-t"}), fallback_cost=10.0, baseline=checklist)

    lift = card["cells"][0]["lift"]
    assert lift["yes_accuracy"] == pytest.approx(1.0 - 0.5)
    assert lift["coverage"] == pytest.approx(1.0 - 2 / 3)


def test_agreement_is_measured_wherever_both_scorers_scored_and_flags_the_gate():
    records = [
        _record(str(i), site="anywhere", first={"car": "yes"}, second={"car": "yes" if i < 8 else "no"})
        for i in range(10)
    ]
    predictions = {r["stop_id"]: {"car": "yes"} for r in records}

    card = scorecard(records, predictions, frozenset(), fallback_cost=0.0)

    agreement = card["cells"][0]["agreement"]
    assert (agreement["both_scored"], agreement["rate"], agreement["under_gate"]) == (10, 0.8, True)
    assert "(under 85%)" in render_markdown(card)


def test_nothing_to_score_says_so_and_decides_nothing():
    card = scorecard([], {}, frozenset(), fallback_cost=35.0)

    text = render_markdown(card)
    assert "Nothing to score" in text
    assert "nothing here passes or fails" in text
    with pytest.raises(ValueError):
        scorecard([], {}, frozenset(), fallback_cost=-1.0)
