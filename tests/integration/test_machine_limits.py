"""DE-2: machine limits are versioned, and a label cannot save without them.

`docs/ROADMAP_1.5.md` DE-2's done-when is that a label refuses to save without a
limits version. These run against the migrated schema, because the refusal is
the database's: a NOT NULL and a foreign key on (version, machine), not a check
in code that a second writer could skip.
"""
import copy
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.driver import Driver
from app.models.hub import Hub
from app.models.machine_label import MACHINES, MachineLabel
from app.models.route import Route
from app.models.stop import Stop
from app.record.machine_limits import (
    LIMIT_FIELDS,
    MachineLimitsError,
    add_machine_limits_version,
    limits_document,
    machine_limits,
    machine_limits_versions,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


async def _a_stop(db_session) -> Stop:
    hub = Hub(id=uuid.uuid4(), name="Limits Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    driver = Driver(
        hub_id=hub.id, name="Sam O.", phone=f"+1555{uuid.uuid4().int % 10**7:07d}",
        vehicle_capacity_units=5,
    )
    db_session.add(driver)
    await db_session.flush()
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)
    await db_session.flush()
    stop = Stop(route_id=route.id, stop_type="dropoff", sequence=1, status="completed")
    db_session.add(stop)
    await db_session.flush()
    return stop


async def _v1_machines(db_session) -> dict:
    return copy.deepcopy(limits_document(await machine_limits(db_session, "v1"))["machines"])


async def test_the_seed_is_one_assumed_version_covering_every_machine(db_session):
    limits = await machine_limits(db_session)

    assert set(limits) == set(MACHINES)
    assert {row.version for row in limits.values()} == {"v1"}
    assert {row.basis for row in limits.values()} == {"assumed"}
    # The open question the version exists for: 2.5 kg, not the other document's 9 lb.
    assert float(limits["drone"].max_weight_kg) == 2.5
    assert "9 lb" in limits["drone"].source
    assert limits["sidewalk_robot"].needs_step_free_path is True
    assert limits["drone"].needs_open_ground is True
    assert limits["car"].max_walk_distance_m == 50.0


async def test_a_label_with_no_limits_version_does_not_save(db_session):
    stop = await _a_stop(db_session)
    db_session.add(MachineLabel(stop_id=stop.id, machine="drone", verdict="no", scorer_id="scorer-1"))

    with pytest.raises(IntegrityError, match="machine_limits_version"):
        await db_session.flush()


async def test_a_label_cannot_cite_a_version_that_does_not_exist(db_session):
    stop = await _a_stop(db_session)
    db_session.add(
        MachineLabel(
            stop_id=stop.id, machine="car", verdict="yes", scorer_id="scorer-1",
            machine_limits_version="v9",
        )
    )

    with pytest.raises(IntegrityError, match="fk_machine_labels_limits"):
        await db_session.flush()


async def test_a_label_citing_v1_saves(db_session):
    stop = await _a_stop(db_session)
    for machine in MACHINES:
        db_session.add(
            MachineLabel(
                stop_id=stop.id, machine=machine, verdict="cant_tell", scorer_id="scorer-1",
                machine_limits_version="v1",
            )
        )
    await db_session.flush()


async def test_a_new_version_becomes_the_latest_and_v1_is_untouched(db_session):
    machines = await _v1_machines(db_session)
    machines["drone"].update(
        max_weight_kg=4.1, basis="operator_reported", source="The drone operator, by email."
    )

    await add_machine_limits_version(db_session, version="v2", machines=machines, recorded_by="rich")

    latest = await machine_limits(db_session)
    assert {row.version for row in latest.values()} == {"v2"}
    assert float(latest["drone"].max_weight_kg) == 4.1
    assert latest["drone"].basis == "operator_reported"
    assert latest["car"].basis == "assumed"
    assert float((await machine_limits(db_session, "v1"))["drone"].max_weight_kg) == 2.5
    assert [v for v, _, _ in await machine_limits_versions(db_session)] == ["v1", "v2"]


async def test_a_version_is_never_edited(db_session):
    with pytest.raises(MachineLimitsError, match="never edited"):
        await add_machine_limits_version(
            db_session, version="v1", machines=await _v1_machines(db_session), recorded_by="rich"
        )


async def test_a_version_must_state_every_machine_and_every_limit(db_session):
    machines = await _v1_machines(db_session)
    del machines["car"]
    del machines["sidewalk_robot"]["needs_step_free_path"]
    machines["drone"]["max_size_class"] = "shoebox"
    machines["drone"]["basis"] = "guessed"

    with pytest.raises(MachineLimitsError) as refused:
        await add_machine_limits_version(db_session, version="v2", machines=machines, recorded_by="rich")

    message = str(refused.value)
    # Every problem at once, so a file with several mistakes takes one round trip.
    assert "missing car" in message
    assert "sidewalk_robot: missing needs_step_free_path" in message
    assert "drone: max_size_class" in message
    assert "drone: basis" in message
    assert await machine_limits(db_session, "v2") == {}


async def test_the_export_is_what_add_reads_back(db_session):
    document = limits_document(await machine_limits(db_session, "v1"))

    assert document["version"] == "v1"
    assert list(document["machines"]) == list(MACHINES)
    for limits in document["machines"].values():
        assert list(limits) == list(LIMIT_FIELDS)
    # Through JSON and back in, unchanged, as a new version.
    await add_machine_limits_version(
        db_session, version="v1-copy", machines=json.loads(json.dumps(document))["machines"],
        recorded_by="test",
    )
    assert limits_document(await machine_limits(db_session, "v1-copy"))["machines"] == document["machines"]


def test_the_command_shows_the_seed(_migration_applied):
    """The operator's way in runs end to end against the migrated database."""
    shown = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "machine_limits.py"), "show", "--version", "v1"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    assert "machine limits v1" in shown.stdout
    assert "sidewalk_robot" in shown.stdout
