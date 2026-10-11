"""DE-10: the checklist baseline's verdicts, on invented records.

The limits are `v1`'s as `scripts/machine_limits.py export` writes them, copied
here so the test needs no database - the rules are what is under test.
"""
import copy

from ml.modality.baseline import UNGROUPED, render_markdown, run
from ml.modality.checklist import (
    CANT_TELL,
    DOOR_UNKNOWN,
    HOURS_UNKNOWN,
    NO,
    NO_SET_DOWN,
    STEPS,
    STOP_TOO_FAR,
    TOO_HEAVY,
    WEIGHT_UNKNOWN,
    YES,
    score,
    score_record,
)

LIMITS = {
    "version": "v1",
    "machines": {
        "drone": {
            "max_weight_kg": 2.5, "max_size_class": "small", "hazmat_or_liquid_allowed": False,
            "needs_step_free_path": False, "needs_continuous_sidewalk": False,
            "needs_open_ground": True, "max_walk_distance_m": None,
        },
        "sidewalk_robot": {
            "max_weight_kg": 9.0, "max_size_class": "medium", "hazmat_or_liquid_allowed": False,
            "needs_step_free_path": True, "needs_continuous_sidewalk": True,
            "needs_open_ground": False, "max_walk_distance_m": None,
        },
        "car": {
            "max_weight_kg": 25.0, "max_size_class": "large", "hazmat_or_liquid_allowed": False,
            "needs_step_free_path": False, "needs_continuous_sidewalk": False,
            "needs_open_ground": False, "max_walk_distance_m": 50.0,
        },
    },
}

GOLD = {
    "location_id": "site-1",
    "group": ["body_shop", "suburb"],
    "item": {"weight_kg": 1.4, "size_class": "small", "hazmat_or_liquid": False},
    "handoff": {
        "stop_legal": True, "walk_distance_m": 18.0, "steps_or_ramps": "none",
        "continuous_sidewalk": True, "obstructions": [], "drone_open_ground": True, "overhead": [],
    },
    "site": {"receiving_hours": {"mon": [["08:00", "17:00"]]}, "who_receives": "counter_staff"},
}


def _record(**changes):
    record = copy.deepcopy(GOLD)
    for path, value in changes.items():
        section, key = path.split("__")
        record[section][key] = value
    return record


def test_a_fully_known_light_item_at_an_open_door_is_yes_for_every_machine():
    verdicts = score_record(GOLD, LIMITS)

    assert {m: v.verdict for m, v in verdicts.items()} == {"drone": YES, "sidewalk_robot": YES, "car": YES}


def test_one_no_decides_it_and_names_the_reason():
    verdict = score(_record(item__weight_kg=4.2), "drone", LIMITS["machines"]["drone"])

    assert (verdict.verdict, verdict.reasons) == (NO, [TOO_HEAVY])


def test_the_weight_limit_is_inclusive():
    assert score(_record(item__weight_kg=2.5), "drone", LIMITS["machines"]["drone"]).verdict == YES


def test_an_unknown_without_a_no_is_cant_tell_with_what_to_collect():
    verdict = score(_record(item__weight_kg=None), "car", LIMITS["machines"]["car"])

    assert (verdict.verdict, verdict.missing) == (CANT_TELL, [WEIGHT_UNKNOWN])


def test_a_no_still_lists_the_missing_facts():
    verdict = score(
        _record(item__weight_kg=30.0, handoff__obstructions=None), "car", LIMITS["machines"]["car"]
    )

    assert verdict.verdict == NO
    assert verdict.missing == [DOOR_UNKNOWN]


def test_each_machine_is_asked_only_its_own_questions():
    # Steps stop the robot and no one else; a wire overhead stops the drone and
    # no one else; a long walk stops the car and no one else.
    steps = score_record(_record(handoff__steps_or_ramps="steps"), LIMITS)
    wires = score_record(_record(handoff__overhead=["wires"]), LIMITS)
    far = score_record(_record(handoff__walk_distance_m=80.0), LIMITS)

    assert {m: v.verdict for m, v in steps.items()} == {"drone": YES, "sidewalk_robot": NO, "car": YES}
    assert steps["sidewalk_robot"].reasons == [STEPS]
    assert {m: v.verdict for m, v in wires.items()} == {"drone": NO, "sidewalk_robot": YES, "car": YES}
    assert wires["drone"].reasons == [NO_SET_DOWN]
    assert {m: v.verdict for m, v in far.items()} == {"drone": YES, "sidewalk_robot": YES, "car": NO}
    assert far["car"].reasons == [STOP_TOO_FAR]


def test_an_imported_record_is_cant_tell_everywhere_and_lists_the_collection():
    # What Experiment 0 holds: nothing weighed, no handoff point captured, no hours.
    imported = {"location_id": "site-nj", "group": [None, None], "item": {}, "handoff": {}, "site": {}}

    verdicts = score_record(imported, LIMITS)

    assert {v.verdict for v in verdicts.values()} == {CANT_TELL}
    assert HOURS_UNKNOWN in verdicts["car"].missing
    assert "stop_point_unknown" in verdicts["car"].missing


def test_the_run_reports_test_sites_and_training_apart():
    records = [GOLD, _record(item__weight_kg=None), dict(GOLD, location_id="site-2", group=[None, None])]

    report = run(records, LIMITS, frozenset({"site-1"}))

    assert report["test"]["records"] == 2
    assert report["training"]["records"] == 1
    drone_test = next(c for c in report["test"]["cells"] if c["machine"] == "drone")
    assert (drone_test["yes"], drone_test["cant_tell"], drone_test["coverage"]) == (1, 1, 0.5)
    assert drone_test["missing"] == {WEIGHT_UNKNOWN: 1}
    assert report["training"]["cells"][0]["group"] == UNGROUPED
    assert "| drone | body_shop / suburb | 2 | 1 | 0 | 1 | 50% |" in render_markdown(report)
