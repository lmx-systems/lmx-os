"""DE-19: an illustrative second supplier, made from open data and labelled as made."""
import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from ml.analytics.illustrative import STAMP
from ml.analytics.supplier_b import Receiver, check_spec, density, generate, summary

SPEC = {
    "name": "test supplier",
    "min_confidence": 0.7,
    "categories": {
        "hvac_service": {"share": 3, "source": "test"},
        "plumbing": {"share": 1, "source": "test"},
    },
    "orders_per_business_day": {"value": 50, "source": "test"},
    "business_days": 4,
    "weight_lb_quantiles": {"points": [[0, 1], [0.5, 10], [1, 100]], "source": "test"},
    "activity_sigma": 1.0,
}
RECEIVERS = [Receiver(f"h{i}", "hvac_service", 30.5 + i / 1000, -97.6) for i in range(20)] + [
    Receiver(f"p{i}", "plumbing", 30.4, -97.7 + i / 1000) for i in range(10)
] + [Receiver("x", "bakery", 30.5, -97.6)]
HOURS = [0] * 9 + [1] * 8 + [0] * 7  # 09:00 to 16:00 only


def test_the_same_inputs_make_the_same_orders():
    assert generate(RECEIVERS, SPEC, HOURS, seed=1) == generate(RECEIVERS, SPEC, HOURS, seed=1)
    assert generate(RECEIVERS, SPEC, HOURS, seed=1) != generate(RECEIVERS, SPEC, HOURS, seed=2)


def test_orders_follow_the_spec_mix_hours_and_receivers():
    orders = generate(RECEIVERS, SPEC, HOURS, seed=1)

    assert len(orders) == 200
    by_category = Counter(o.category for o in orders)
    assert 0.65 < by_category["hvac_service"] / 200 < 0.85
    # Only the spec's categories receive, and only in the profile's hours.
    assert "bakery" not in by_category
    assert {o.hour for o in orders} <= set(range(9, 17))
    assert len({o.order_id for o in orders}) == 200


def test_weights_follow_the_quantile_curve():
    orders = generate(RECEIVERS, {**SPEC, "business_days": 40}, HOURS, seed=3)
    pounds = sorted(o.weight_kg / 0.45359237 for o in orders)

    assert pounds[0] >= 1 - 0.01 and pounds[-1] <= 100 + 0.01
    assert 8 < pounds[len(pounds) // 2] < 12  # the curve's median is 10 lb


def test_a_spec_must_be_sourced_and_its_curve_must_rise():
    unsourced = copy.deepcopy(SPEC)
    unsourced["categories"]["plumbing"]["source"] = ""
    falling = copy.deepcopy(SPEC)
    falling["weight_lb_quantiles"]["points"] = [[0, 10], [0.5, 5], [1, 100]]
    short = copy.deepcopy(SPEC)
    short["weight_lb_quantiles"]["points"] = [[0.1, 1], [1, 100]]

    assert any("plumbing: no source" in p for p in check_spec(unsourced))
    assert any("must rise" in p for p in check_spec(falling))
    assert any("from share 0 to share 1" in p for p in check_spec(short))
    with pytest.raises(ValueError, match="no source"):
        generate(RECEIVERS, unsourced, HOURS, seed=1)


def test_density_counts_a_cell_shared_only_when_both_deliver_there_in_the_hour():
    here, far = (30.50, -97.60), (30.70, -97.90)
    a = [(here, 10), (here, 10), (far, 11)]
    b = [(here, 10), (here, 11)]

    reading = density(a, b, days_a=1, days_b=1)
    ten, eleven = reading["hours"]

    assert reading["stamp"] == STAMP
    assert (ten["hour"], ten["cells_shared"], ten["b_drops_in_shared_cells"]) == (10, 1, 1.0)
    assert ten["drops_per_cell_a"] == 2.0 and ten["drops_per_cell_together"] == 3.0
    # At 11:00 the two suppliers are in different cells: nothing shared.
    assert (eleven["cells_with_any"], eleven["cells_shared"]) == (2, 0)


def test_density_is_per_business_day():
    reading = density([((30.5, -97.6), 9)] * 10, [((30.5, -97.6), 9)] * 4, days_a=5, days_b=2)

    hour = reading["hours"][0]
    assert (hour["drops_a"], hour["drops_b"]) == (2.0, 2.0)


def test_the_summary_says_what_each_machine_could_lift():
    about = summary(generate(RECEIVERS, SPEC, HOURS, seed=1))

    assert about["stamp"] == STAMP
    assert 0 < about["share_at_most_2_3_kg"] < about["share_at_most_9_07_kg"] < about["share_at_most_25_kg"] <= 1


def test_the_committed_spec_is_well_formed_and_every_input_names_its_source():
    spec = json.loads((Path(__file__).resolve().parent.parent / "ml" / "analytics" / "supplier_b_spec.json").read_text())

    assert check_spec(spec) == []
    assert abs(sum(c["share"] for c in spec["categories"].values()) - 1) < 0.01
    assert spec["hours_source"] and spec["activity_source"]
