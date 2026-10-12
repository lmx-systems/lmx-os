"""DE-9: the labelling queue - two scorers, the second blind, all three machines at once."""
import uuid

import pytest
from sqlalchemy import select, text

from app.identity import resolve_location
from app.models.driver import Driver
from app.models.handoff_point import HandoffPoint
from app.models.hub import Hub
from app.models.machine_label import MachineLabel
from app.models.route import Route
from app.models.stop import Stop
from app.record.labelling import LabelRefused, labelling_queue, save_verdicts, scorer_agreement
from app.schemas.labelling import LabellingRecordView

pytestmark = pytest.mark.integration

ALL_YES = {m: {"verdict": "yes"} for m in ("drone", "sidewalk_robot", "car")}


async def _record(db_session, *, captured: bool = False, kind="body_shop", setting="suburb") -> uuid.UUID:
    hub = Hub(id=uuid.uuid4(), name="Label Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    driver = Driver(hub_id=hub.id, name="Sam O.", phone=f"+1555{uuid.uuid4().int % 10**7:07d}")
    db_session.add(driver)
    await db_session.flush()
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)
    await db_session.flush()
    location = await resolve_location(db_session, address=f"{uuid.uuid4().int % 9000 + 100} Elm Street, Austin, TX")
    location.kind_of_site, location.setting = kind, setting
    point = HandoffPoint(
        location_id=location.id, source="technician",
        **({"handoff_type": "counter", "stop_legal": True, "walk_distance_m": 12.0, "steps_or_ramps": "none"} if captured else {}),
    )
    db_session.add(point)
    await db_session.flush()
    stop = Stop(route_id=route.id, stop_type="dropoff", sequence=1, status="completed", handoff_point_id=point.id)
    db_session.add(stop)
    await db_session.commit()
    return stop.id


async def test_the_queue_leads_with_the_most_complete_record(db_session):
    bare = await _record(db_session)
    rich = await _record(db_session, captured=True)

    queue = await labelling_queue(db_session, scorer_id="ana")

    assert [r["stop_id"] for r in queue][:2] == [rich, bare] or queue[0]["elements_missing"] <= queue[1]["elements_missing"]
    assert {r["stop_id"] for r in queue} == {bare, rich}


async def test_the_second_scorer_is_blind_and_a_third_is_not_needed(db_session):
    record = await _record(db_session)

    first = await save_verdicts(db_session, stop_id=record, scorer_id="ana", verdicts=ALL_YES, machine_limits_version="v1")
    second = await save_verdicts(
        db_session, stop_id=record, scorer_id="ben",
        verdicts={**ALL_YES, "drone": {"verdict": "no", "reason_codes": ["no_safe_set_down_spot"]}},
        machine_limits_version="v1",
    )

    assert {label.is_blind_second for label in first} == {False}
    assert {label.is_blind_second for label in second} == {True}
    with pytest.raises(LabelRefused, match="two scorers"):
        await save_verdicts(db_session, stop_id=record, scorer_id="cal", verdicts=ALL_YES, machine_limits_version="v1")
    # Nobody who has scored it, and nobody once two have, sees it again.
    assert await labelling_queue(db_session, scorer_id="ana") == []
    assert await labelling_queue(db_session, scorer_id="cal") == []


async def test_a_scorer_scores_a_record_once(db_session):
    record = await _record(db_session)
    await save_verdicts(db_session, stop_id=record, scorer_id="ana", verdicts=ALL_YES, machine_limits_version="v1")

    with pytest.raises(LabelRefused, match="already"):
        await save_verdicts(db_session, stop_id=record, scorer_id="ana", verdicts=ALL_YES, machine_limits_version="v1")


@pytest.mark.parametrize(
    ("verdicts", "complaint"),
    [
        ({"drone": {"verdict": "yes"}}, "every machine"),
        ({**ALL_YES, "car": {"verdict": "no"}}, "say why"),
        ({**ALL_YES, "car": {"verdict": "no", "reason_codes": ["weight_unknown"]}}, "not only a missing fact"),
        ({**ALL_YES, "car": {"verdict": "cant_tell", "reason_codes": ["vibes"]}}, "unknown reason"),
        ({**ALL_YES, "car": {"verdict": "maybe"}}, "verdict must be"),
    ],
)
async def test_a_verdict_says_why_in_the_checklists_words(db_session, verdicts, complaint):
    record = await _record(db_session)

    with pytest.raises(LabelRefused, match=complaint):
        await save_verdicts(db_session, stop_id=record, scorer_id="ana", verdicts=verdicts, machine_limits_version="v1")


async def test_a_verdict_cites_limits_that_exist(db_session):
    record = await _record(db_session)

    with pytest.raises(LabelRefused, match="v9"):
        await save_verdicts(db_session, stop_id=record, scorer_id="ana", verdicts=ALL_YES, machine_limits_version="v9")


async def test_the_queue_shows_no_one_elses_verdict(db_session):
    record = await _record(db_session)
    await save_verdicts(db_session, stop_id=record, scorer_id="ana", verdicts=ALL_YES, machine_limits_version="v1")

    [row] = await labelling_queue(db_session, scorer_id="ben")

    assert row["stop_id"] == record
    assert not {"verdict", "reason_codes", "scorer_id", "machine"} & set(LabellingRecordView.model_fields)


async def test_a_first_scorers_three_verdicts_complete_the_qualifier(db_session):
    record = await _record(db_session)
    await save_verdicts(db_session, stop_id=record, scorer_id="ana", verdicts=ALL_YES, machine_limits_version="v1")

    has = await db_session.scalar(
        text("SELECT has_autonomy_qualifier FROM record_tiers WHERE stop_id = :s"), {"s": record}
    )
    assert has is True
    labels = (await db_session.scalars(select(MachineLabel).where(MachineLabel.stop_id == record))).all()
    assert {label.machine_limits_version for label in labels} == {"v1"}


async def test_agreement_is_per_machine_and_group_and_flags_the_gate(db_session):
    for _ in range(4):
        record = await _record(db_session)
        await save_verdicts(db_session, stop_id=record, scorer_id="ana", verdicts=ALL_YES, machine_limits_version="v1")
    # Ben agrees on every car and robot, and on half the drones.
    records = [r["stop_id"] for r in await labelling_queue(db_session, scorer_id="ben", limit=10)]
    for index, record in enumerate(records):
        drone = {"verdict": "yes"} if index % 2 else {"verdict": "no", "reason_codes": ["too_heavy"]}
        await save_verdicts(
            db_session, stop_id=record, scorer_id="ben", verdicts={**ALL_YES, "drone": drone}, machine_limits_version="v1"
        )

    cells = {c.machine: c for c in (await scorer_agreement(db_session)).cells}

    assert (cells["car"].both_scored, cells["car"].rate, cells["car"].under_gate) == (4, 1.0, False)
    assert (cells["drone"].rate, cells["drone"].under_gate) == (0.5, True)
    assert cells["drone"].group == "body_shop / suburb"
