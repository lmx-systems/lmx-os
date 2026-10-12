"""The checklist baseline: rules from the machine limits give a verdict (`DE-10`).

The bar any modality model must beat, as `PRD-3`'s shrunk quantile is for dwell.
It is checklist v0.1 from the model, dataset and autonomy design (25 Sep 2026),
read against one version of the machine limits (`DE-2`):

    #   question                                            asked of
    1   is the item within the weight limit                 every machine
    2   within the size limit                               every machine
    3   free of a hazardous or liquid flag it may not carry every machine
    4   a legal place to stop                               a machine with a walk limit (car)
    5   the stop within the walk limit of the door          a machine with a walk limit (car)
    6   a step-free path, and a sidewalk all the way        a machine that needs them (robot)
    7   open, flat ground near the door, nothing overhead   a machine that needs it (drone)
    8   nothing in the way: gate, fence, code, vehicles     every machine
    9   someone there to receive it                         every machine

Questions 10 (appointment or signature) and 11 (one building, or the right one
clear) have no field in the golden record yet, so they are not asked; a "yes"
here is a yes on the nine that are.

**A technician's visit carries no item** (`DE-6`, D-VISIT), so questions 1-3
are not asked of one: its verdict is about the door - could this machine serve
it with an item within its limits - and an item it never had is not a fact to
go and collect.

**The weakest answer wins.** One "no" makes the verdict no; otherwise one
unknown makes it can't tell; otherwise yes. A "no" carries the reasons that
failed. Missing facts are reported apart from real noes, as the design says,
because they are a different thing: a "no" says the machine cannot do it, a
missing fact says what to go and find out. Both are listed even when a "no"
already decides the verdict, because the missing facts are the collection list.

Records and limits arrive as plain dictionaries - the shape the app's exports
write - so this runs anywhere, with the standard library.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

SIZE_ORDER = ("envelope", "small", "medium", "large", "oversize")

YES, NO, CANT_TELL = "yes", "no", "cant_tell"

# Missing-data codes: what to collect next.
WEIGHT_UNKNOWN = "weight_unknown"
SIZE_UNKNOWN = "size_unknown"
HAZMAT_UNKNOWN = "hazmat_unknown"
STOP_POINT_UNKNOWN = "stop_point_unknown"
DOOR_UNKNOWN = "door_unknown"
HOURS_UNKNOWN = "hours_unknown"

# Reasons for a "no".
TOO_HEAVY = "too_heavy"
TOO_BIG = "too_big"
HAZARDOUS = "hazardous_or_liquid"
NO_LEGAL_STOP = "no_legal_stop"
STOP_TOO_FAR = "stop_too_far_from_door"
STEPS = "steps_at_entrance"
NO_SIDEWALK = "no_sidewalk_route"
NO_SET_DOWN = "no_safe_set_down_spot"
OBSTRUCTED = "gated_or_obstructed"


@dataclass
class Verdict:
    machine: str
    verdict: str
    reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _blank(value) -> bool:
    return value is None


def score(record: Mapping, machine: str, limits: Mapping) -> Verdict:
    """One record, one machine, one version's limits for that machine."""
    item = record.get("item") or {}
    handoff = record.get("handoff") or {}
    site = record.get("site") or {}
    reasons: list[str] = []
    missing: list[str] = []

    def check(known: bool, ok: bool, missing_code: str, reason: str) -> None:
        if not known:
            if missing_code not in missing:
                missing.append(missing_code)
        elif not ok:
            reasons.append(reason)

    # 1-3: the item. A visit has none.
    if record.get("stop_type") != "visit":
        weight = item.get("weight_kg")
        check(not _blank(weight), weight is not None and weight <= limits["max_weight_kg"], WEIGHT_UNKNOWN, TOO_HEAVY)
        size = item.get("size_class")
        check(
            size in SIZE_ORDER,
            size in SIZE_ORDER and SIZE_ORDER.index(size) <= SIZE_ORDER.index(limits["max_size_class"]),
            SIZE_UNKNOWN, TOO_BIG,
        )
        hazmat = item.get("hazmat_or_liquid")
        check(not _blank(hazmat), not hazmat or limits["hazmat_or_liquid_allowed"], HAZMAT_UNKNOWN, HAZARDOUS)

    # 4-5: a vehicle that stops and is met there.
    if limits["max_walk_distance_m"] is not None:
        legal = handoff.get("stop_legal")
        check(not _blank(legal), bool(legal), STOP_POINT_UNKNOWN, NO_LEGAL_STOP)
        walk = handoff.get("walk_distance_m")
        check(
            not _blank(walk), walk is not None and walk <= limits["max_walk_distance_m"],
            STOP_POINT_UNKNOWN, STOP_TOO_FAR,
        )

    # 6: a machine that travels to the door on the ground.
    if limits["needs_step_free_path"]:
        steps = handoff.get("steps_or_ramps")
        check(not _blank(steps), steps in ("none", "ramp"), DOOR_UNKNOWN, STEPS)
    if limits["needs_continuous_sidewalk"]:
        sidewalk = handoff.get("continuous_sidewalk")
        check(not _blank(sidewalk), bool(sidewalk), DOOR_UNKNOWN, NO_SIDEWALK)

    # 7: a machine that sets down from the air.
    if limits["needs_open_ground"]:
        ground, overhead = handoff.get("drone_open_ground"), handoff.get("overhead")
        known = not _blank(ground) and isinstance(overhead, list)
        check(known, known and bool(ground) and not overhead, DOOR_UNKNOWN, NO_SET_DOWN)

    # 8: anything in the way. An empty list was checked and is clear.
    obstructions = handoff.get("obstructions")
    check(isinstance(obstructions, list), not obstructions, DOOR_UNKNOWN, OBSTRUCTED)

    # 9: someone to receive it. Known hours and a known receiver pass; when in
    # the day the machine would arrive is not on the record yet.
    receiving = site.get("receiving_hours") is not None and site.get("who_receives") is not None
    check(receiving, True, HOURS_UNKNOWN, "")

    verdict = NO if reasons else CANT_TELL if missing else YES
    return Verdict(machine=machine, verdict=verdict, reasons=reasons, missing=missing)


def score_record(record: Mapping, limits_document: Mapping) -> dict[str, Verdict]:
    """Every machine in a limits version, for one record."""
    return {
        machine: score(record, machine, limits)
        for machine, limits in limits_document["machines"].items()
    }
