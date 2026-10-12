"""The labelling queue: a person's verdict on each record, per machine (`DE-9`).

`docs/ROADMAP_1.5.md` DE-9. Scorers judge whether a drone, a sidewalk robot and
a car could have made each delivery - yes, no or can't tell, with the checklist
reasons - against the current machine limits (`DE-2`). Those verdicts are the
autonomy qualifier, the fifth element of a record's tier (`record_tiers`), and
the labels any modality model is scored against (`DE-11`).

**Two scorers, the second blind.** Whoever scores a record first is its first
scorer; the next is the blind second (`is_blind_second`), and nothing here ever
shows one scorer another's verdict - the queue leaves out what others said, and
saving says nothing about them either. Agreement between the two is the ceiling
on any accuracy claim: two people who agree 93% of the time cannot support a
98% model. A record needs no more than two.

**Most complete first.** A record missing every fact can only be "can't tell"
three times over, which costs a scorer a minute and teaches nothing, so the
queue leads with records that have the most captured.

The single-scorer `M5` answers on `receiver_profiles` are not migrated in. They
are one driver's answers per dock from the dock survey, with no limits version
and no reasons, and a record's label is a scored checklist against stated
limits; copying them across would give hundreds of records a first scorer who
never saw them.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.machine_label import MACHINES, MISSING_DATA, REASON_CODES, VERDICTS, MachineLabel
from app.models.stop import Stop
from app.record.machine_limits import machine_limits

AGREEMENT_GATE = 0.85
SCORERS_PER_RECORD = 2

_QUEUE = text(
    """
    SELECT rt.stop_id, rt.tier, rt.elements_missing, rt.record_source, rt.recorded_at,
           l.address, l.region, l.kind_of_site, l.setting,
           hp.handoff_type, hp.stop_type, hp.stop_legal, hp.walk_distance_m, hp.steps_or_ramps,
           hp.continuous_sidewalk, hp.obstructions, hp.drone_open_ground, hp.overhead,
           hp.photo_approach, hp.photo_stop_point, hp.photo_path, hp.photo_handoff,
           o.item_description, o.item_weight_kg, o.item_size_class, o.item_hazmat_or_liquid,
           rp.receiving_hours, rp.who_receives
    FROM record_tiers rt
    LEFT JOIN handoff_points hp ON hp.id = rt.handoff_point_id
    LEFT JOIN locations l ON l.id = hp.location_id
    LEFT JOIN orders o ON o.id = rt.order_id
    LEFT JOIN receiver_profiles rp ON rp.location_id = hp.location_id
    WHERE (CAST(:region AS text) IS NULL OR l.region = :region)
      AND NOT EXISTS (
          SELECT 1 FROM machine_labels mine
          WHERE mine.stop_id = rt.stop_id AND mine.scorer_id = :scorer
      )
      AND (
          SELECT count(DISTINCT ml.scorer_id) FROM machine_labels ml WHERE ml.stop_id = rt.stop_id
      ) < :enough
    ORDER BY rt.elements_missing, rt.recorded_at DESC, rt.stop_id
    LIMIT :limit
    """
)


_AGREEMENT = text(
    """
    SELECT ml.stop_id, ml.machine, ml.verdict, ml.is_blind_second, l.kind_of_site, l.setting
    FROM machine_labels ml
    LEFT JOIN handoff_points hp ON hp.id = ml.handoff_point_id
    LEFT JOIN locations l ON l.id = hp.location_id
    """
)


class LabelRefused(ValueError):
    """The verdicts cannot be saved as given."""


@dataclass
class Agreement:
    machine: str
    group: str
    both_scored: int = 0
    agreed: int = 0

    @property
    def rate(self) -> float | None:
        return self.agreed / self.both_scored if self.both_scored else None

    @property
    def under_gate(self) -> bool | None:
        return None if self.rate is None else self.rate < AGREEMENT_GATE


@dataclass
class AgreementReport:
    cells: list[Agreement] = field(default_factory=list)


async def labelling_queue(
    session: AsyncSession, *, scorer_id: str, region: str | None = None, limit: int = 25
) -> list[dict]:
    """Records this scorer has not scored and fewer than two people have,
    most complete first. Never includes anyone's verdicts."""
    rows = await session.execute(
        _QUEUE,
        {"scorer": scorer_id, "region": region, "enough": SCORERS_PER_RECORD, "limit": limit},
    )
    return [dict(row) for row in rows.mappings().all()]


async def save_verdicts(
    session: AsyncSession,
    *,
    stop_id: uuid.UUID,
    scorer_id: str,
    verdicts: dict[str, dict],
    machine_limits_version: str,
) -> list[MachineLabel]:
    """One scorer's verdict for every machine on one record. Flushes; the caller
    commits.

    `verdicts` maps each machine to `{"verdict", "reason_codes", "confidence"}`.
    All three machines at once, because the autonomy qualifier is a first
    scorer's verdict on all three, and a record half-scored by one person and
    finished by another would mix two judgements into one qualifier.
    """
    stop = await session.get(Stop, stop_id)
    if stop is None or stop.stop_type not in ("dropoff", "visit"):
        raise LabelRefused("no such record: a record is a drop-off or visit stop")
    problems = []
    if set(verdicts) != set(MACHINES):
        problems.append(f"give a verdict for every machine: {', '.join(MACHINES)}")
    for machine, given in verdicts.items():
        verdict = given.get("verdict")
        reasons = list(given.get("reason_codes") or [])
        if verdict not in VERDICTS:
            problems.append(f"{machine}: verdict must be one of {', '.join(VERDICTS)}")
        unknown = [r for r in reasons if r not in REASON_CODES]
        if unknown:
            problems.append(f"{machine}: unknown reason {', '.join(unknown)}")
        if verdict in ("no", "cant_tell") and not reasons:
            problems.append(f"{machine}: say why it is not a yes")
        if verdict == "no" and reasons and all(r in MISSING_DATA for r in reasons):
            problems.append(f"{machine}: a no needs a reason it cannot be done, not only a missing fact")
        confidence = given.get("confidence")
        if confidence is not None and not 0 <= confidence <= 1:
            problems.append(f"{machine}: confidence is between 0 and 1")
    if not (await machine_limits(session, machine_limits_version)):
        problems.append(f"no machine limits version {machine_limits_version!r}")
    if problems:
        raise LabelRefused("; ".join(problems))

    scorers = set(
        (await session.scalars(select(MachineLabel.scorer_id).where(MachineLabel.stop_id == stop_id))).all()
    )
    if scorer_id in scorers:
        raise LabelRefused("you have scored this record already")
    if len(scorers) >= SCORERS_PER_RECORD:
        raise LabelRefused("this record has its two scorers")

    labels = [
        MachineLabel(
            stop_id=stop_id,
            handoff_point_id=stop.handoff_point_id,
            machine=machine,
            verdict=verdicts[machine]["verdict"],
            reason_codes=list(verdicts[machine].get("reason_codes") or []),
            confidence=verdicts[machine].get("confidence"),
            scorer_id=scorer_id,
            is_blind_second=bool(scorers),
            machine_limits_version=machine_limits_version,
        )
        for machine in MACHINES
    ]
    session.add_all(labels)
    await session.flush()
    return labels


async def scorer_agreement(session: AsyncSession) -> AgreementReport:
    """First scorer against blind second, per machine and group (kind of site x
    setting), wherever both scored. Under 85% the labels are too subjective to
    train on; fix the checklist question before collecting more."""
    rows = (await session.execute(_AGREEMENT)).all()
    pairs: dict = defaultdict(dict)
    groups: dict = {}
    for stop_id, machine, verdict, blind, kind, setting in rows:
        pairs[(stop_id, machine)]["second" if blind else "first"] = verdict
        groups[stop_id] = f"{kind} / {setting}" if kind and setting else "(kind or setting unknown)"
    cells: dict = {}
    for (stop_id, machine), pair in pairs.items():
        if "first" not in pair or "second" not in pair:
            continue
        key = (machine, groups[stop_id])
        cell = cells.setdefault(key, Agreement(machine=machine, group=key[1]))
        cell.both_scored += 1
        cell.agreed += pair["first"] == pair["second"]
    return AgreementReport(cells=[cells[k] for k in sorted(cells)])
