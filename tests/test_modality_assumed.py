"""DE-17: the partner mock-up's eligibility: the checklist as measured and as assumed, on invented records."""
import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from ml.analytics.illustrative import STAMP
from ml.modality.assumed import check_sheet, draw_sample, fill, render_markdown, run
from ml.modality.checklist import CANT_TELL, YES, score_record
from tests.test_modality_checklist import LIMITS

SHEET = {
    "version": "t1",
    "assumptions": [
        {"id": "I1", "where": {"pieces": [1, 1]}, "sets": {"item.weight_kg": 1.0, "item.size_class": "small"}, "source": "test"},
        {"id": "I2", "where": {"pieces": [2, None]}, "sets": {"item.weight_kg": 20.0, "item.size_class": "large"}, "source": "test"},
        {"id": "H1", "where": {}, "sets": {"item.hazmat_or_liquid": False}, "source": "test"},
        {
            "id": "D1",
            "where": {"kind_of_site": ["shop"]},
            "sets": {
                "handoff.stop_legal": True, "handoff.walk_distance_m": 10, "handoff.steps_or_ramps": "none",
                "handoff.continuous_sidewalk": True, "handoff.drone_open_ground": True,
                "handoff.overhead": [], "handoff.obstructions": [],
            },
            "source": "test",
        },
        {"id": "R1", "where": {}, "sets": {"site.receiving_hours": "9-5", "site.who_receives": "staff"}, "source": "test"},
    ],
}


def _record(i, pieces, kind="shop", **item):
    return {
        "stop_id": f"r{i}",
        "stop_type": "dropoff",
        "kind_of_site": kind,
        "pieces": pieces,
        "item": {"weight_kg": None, "size_class": None, "hazmat_or_liquid": None, **item},
        "handoff": {},
        "site": {"receiving_hours": None, "who_receives": None},
    }


def test_an_assumption_fills_only_a_null_and_names_itself():
    record = _record(0, 1, weight_kg=3.0)

    filled, used = fill(record, SHEET)

    assert filled["item"]["weight_kg"] == 3.0  # measured wins
    assert filled["item"]["size_class"] == "small"
    assert used["item.size_class"] == "I1" and "item.weight_kg" not in used
    assert record["item"]["size_class"] is None  # the record itself is untouched


def test_a_range_with_no_top_and_a_list_of_values_both_match():
    _, used = fill(_record(1, 40), SHEET)
    _, other_kind = fill(_record(2, 40, kind="yard"), SHEET)

    assert used["item.weight_kg"] == "I2"
    assert "handoff.stop_legal" in used and "handoff.stop_legal" not in other_kind


def test_leaving_an_assumption_out_leaves_its_facts_null():
    filled, used = fill(_record(0, 1), SHEET, without="H1")

    assert filled["item"]["hazmat_or_liquid"] is None
    assert "item.hazmat_or_liquid" not in used


def test_a_sheet_with_problems_is_refused_whole():
    bad = copy.deepcopy(SHEET)
    del bad["version"]
    bad["assumptions"][0]["source"] = ""
    bad["assumptions"][1]["id"] = "I1"
    bad["assumptions"][2]["sets"] = {"weight": 1}

    problems = check_sheet(bad)

    assert len(problems) == 4
    with pytest.raises(ValueError):
        run([_record(0, 1)], LIMITS, bad)


def test_measured_decides_nothing_and_assumed_says_what_it_rests_on():
    records = [_record(0, 1), _record(1, 1), _record(2, 5), _record(3, None)]

    report = run(records, LIMITS, SHEET)

    assert report["stamp"] == STAMP
    assert report["measured"]["drone"]["cant_tell"] == 4
    assert report["measured"]["drone"]["missing"]["weight_unknown"] == 4
    drone = report["assumed"]["drone"]
    # One piece: a light, small item at an open door - yes. Five pieces: too heavy - no.
    # No piece count: the weight stays unknown - can't tell.
    assert (drone["yes"], drone["no"], drone["cant_tell"]) == (2, 1, 1)
    ranking = {r["id"]: r for r in report["assumptions"]}
    # Every decided verdict needs the door, the hours and hazmat; only some need each item rule.
    assert ranking["D1"]["verdicts_resting_on_it"] >= ranking["I1"]["verdicts_resting_on_it"] > 0
    assert report["assumptions"][0]["verdicts_resting_on_it"] == max(
        r["verdicts_resting_on_it"] for r in report["assumptions"]
    )


def test_the_report_agrees_with_the_checklist_record_by_record():
    records = [_record(i, p) for i, p in enumerate([1, 2, 1, 7, None, 1])]

    report = run(records, LIMITS, SHEET)

    for machine in LIMITS["machines"]:
        expected = Counter(score_record(fill(r, SHEET)[0], LIMITS)[machine].verdict for r in records)
        got = report["assumed"][machine]
        assert (got["yes"], got["cant_tell"]) == (expected[YES], expected[CANT_TELL])


def test_zones_count_eligible_drop_offs_for_their_own_machine():
    inside = [{**_record(0, 1), "zones": ["Circle"]}, {**_record(1, 5), "zones": ["Circle"]}]
    outside = [_record(2, 1)]

    report = run(inside + outside, LIMITS, SHEET, {"Circle": "drone", "Elsewhere": "submarine"})

    circle, elsewhere = report["zones"]
    assert (circle["records_inside"], circle["eligible_as_assumed"], circle["eligible_as_measured"]) == (2, 1, 0)
    assert elsewhere == {"zone": "Elsewhere", "machine": "submarine", "records_inside": 0}


def test_the_sample_is_seeded_and_keeps_every_kind():
    records = [_record(i, 1, kind="shop") for i in range(90)] + [_record(90 + i, 1, kind="yard") for i in range(10)]

    first = draw_sample(records, 20, seed=7)

    assert [r["stop_id"] for r in first] == [r["stop_id"] for r in draw_sample(records, 20, seed=7)]
    assert Counter(r["kind_of_site"] for r in first) == {"shop": 18, "yard": 2}
    assert draw_sample(records, 500, seed=7) == records


def test_the_markdown_prints_every_rule_and_its_source():
    text = render_markdown(run([_record(0, 1)], LIMITS, SHEET), title="T", about="A.")

    assert STAMP in text
    for rule in SHEET["assumptions"]:
        assert f"| {rule['id']} |" in text


def test_the_draft_sheet_is_valid_and_says_every_value_is_a_placeholder():
    path = Path(__file__).resolve().parent.parent / "ml" / "modality" / "assumption_sheet_a0.json"
    sheet = json.loads(path.read_text())

    assert check_sheet(sheet) == []
    assert all(rule["source"].startswith("Placeholder") for rule in sheet["assumptions"])
