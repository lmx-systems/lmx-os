"""REC-1: a decision replays exactly, and nothing can rewrite it.

Two clauses in the done-when, and the second is the one with teeth:

  1. A decision replays to reproduce its inputs exactly.
  2. No field may reference data created after the decision.

Clause 2 cannot be demonstrated by reading the code - the obvious wrong
implementation (store an order_id, join back at replay) passes every test that
does not mutate the order in between. So the central test here changes the
world after the decision and checks the replay did not move.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.models.decision_snapshot import MODE_LIVE, MODE_SHADOW, DecisionSnapshot
from app.models.hub import Hub
from app.record import canonical_inputs_hash, record_decision, replay_inputs
from app.schemas.optimizer import CyclePlan, DriverCandidate, StopCandidate

pytestmark = pytest.mark.integration

DECIDED_AT = datetime(2026, 9, 16, 9, 30, tzinfo=timezone.utc)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Decision Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    return hub


def _plan(hub_id: str, *, stops=2, drivers=1, engine="stub_nearest_neighbor") -> CyclePlan:
    return CyclePlan(
        hub_id=hub_id,
        planned_at=DECIDED_AT,
        hub_closed=False,
        held_order_count=stops,
        released_order_ids=[f"order-{i}" for i in range(stops)],
        shop_name_by_order_id={f"order-{i}": f"Shop {i}" for i in range(stops)},
        fleet_snapshot=[],
        stops=[
            StopCandidate(
                stop_id=f"order-{i}",
                order_ids=[f"order-{i}"],
                lat=30.26 + i / 1000,
                lng=-97.74 - i / 1000,
                weight_units=1.0,
                sla_tier="T2",
                collect_by=DECIDED_AT + timedelta(minutes=90),
            )
            for i in range(stops)
        ],
        drivers=[
            DriverCandidate(
                driver_id=f"driver-{i}",
                lat=30.27,
                lng=-97.74,
                capacity_remaining_units=40.0,
            )
            for i in range(drivers)
        ],
        assignments=[],
        unassigned_stop_ids=[f"order-{i}" for i in range(stops)],
        engine=engine,
        plan_duration_seconds=0.02,
    )


class TestReplay:
    async def test_a_decision_replays_to_what_it_saw(self, db_session):
        hub = await _hub(db_session)
        snapshot = await record_decision(db_session, _plan(str(hub.id)))

        replayed = await replay_inputs(db_session, snapshot.id)

        assert replayed["planned_at"] == DECIDED_AT.isoformat()
        assert replayed["released_order_ids"] == ["order-0", "order-1"]
        assert [s["stop_id"] for s in replayed["stops"]] == ["order-0", "order-1"]
        assert replayed["drivers"][0]["capacity_remaining_units"] == 40.0
        assert replayed["shop_name_by_order_id"]["order-0"] == "Shop 0"

    async def test_the_world_changing_afterwards_does_not_change_the_replay(self, db_session):
        """Clause 2, and the only test here that could fail for a real reason.

        The wrong implementation - store an order_id, join back to `orders` at
        replay - passes everything else in this file. It fails here, because
        after the shop is renamed and the driver has moved, a join would
        reconstruct a decision that was never made and present it as the
        record.
        """
        hub = await _hub(db_session)
        plan = _plan(str(hub.id))
        snapshot = await record_decision(db_session, plan)

        # The world moves on: a shop is renamed, a driver drives away, an order
        # is re-tiered. All of these are ordinary.
        plan.shop_name_by_order_id["order-0"] = "Renamed Later Ltd"
        plan.drivers[0].lat = 31.99
        plan.drivers[0].capacity_remaining_units = 0.0
        plan.released_order_ids.append("order-added-later")
        # The two that matter most: an order re-tiered to urgent, and a
        # deadline pushed out. A replay reading these from `orders` would say
        # the optimizer was working to a promise it never had.
        plan.stops[0].sla_tier = "T1"
        plan.stops[0].collect_by = DECIDED_AT + timedelta(days=1)

        replayed = await replay_inputs(db_session, snapshot.id)

        assert replayed["shop_name_by_order_id"]["order-0"] == "Shop 0"
        assert replayed["drivers"][0]["lat"] == 30.27
        assert replayed["drivers"][0]["capacity_remaining_units"] == 40.0
        assert "order-added-later" not in replayed["released_order_ids"]
        assert replayed["stops"][0]["sla_tier"] == "T2"
        assert replayed["stops"][0]["collect_by"] == (
            DECIDED_AT + timedelta(minutes=90)
        ).isoformat()

    async def test_replay_reads_one_row_and_nothing_else(self, db_session):
        """The guarantee stated as a mechanism rather than an intention.

        Every other table is emptied before the replay. If the snapshot needed
        any of them, this would raise rather than return - and clause 2 would
        be false.
        """
        hub = await _hub(db_session)
        snapshot = await record_decision(db_session, _plan(str(hub.id)))
        await db_session.commit()

        # Nothing survives that a join could reach. The hub row goes too: there
        # is no FK to it, precisely so a decision outlives its subject.
        await db_session.execute(
            text("DELETE FROM decision_snapshots WHERE hub_id <> :keep"), {"keep": hub.id}
        )
        await db_session.execute(text("DELETE FROM shop_profiles"))
        await db_session.execute(text("DELETE FROM drivers"))
        await db_session.execute(text("DELETE FROM hubs"))
        await db_session.commit()

        replayed = await replay_inputs(db_session, snapshot.id)
        assert replayed["stops"][0]["stop_id"] == "order-0"

    async def test_an_unknown_snapshot_is_a_lookup_error(self, db_session):
        with pytest.raises(LookupError):
            await replay_inputs(db_session, uuid.uuid4())


class TestTamperEvidence:
    async def test_the_database_refuses_to_update_a_decision(self, db_session):
        """Immutability enforced where it cannot be argued with.

        An audit log the application can rewrite is not an audit log, and the
        moment somebody wants to rewrite one is the moment it matters most.
        """
        hub = await _hub(db_session)
        snapshot = await record_decision(db_session, _plan(str(hub.id)))
        await db_session.commit()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text("UPDATE decision_snapshots SET engine = 'rewritten' WHERE id = :id"),
                {"id": snapshot.id},
            )
        await db_session.rollback()

    async def test_the_database_refuses_to_delete_a_decision(self, db_session):
        hub = await _hub(db_session)
        snapshot = await record_decision(db_session, _plan(str(hub.id)))
        await db_session.commit()

        with pytest.raises(Exception, match="append-only"):
            await db_session.execute(
                text("DELETE FROM decision_snapshots WHERE id = :id"), {"id": snapshot.id}
            )
        await db_session.rollback()

    async def test_a_replay_refuses_a_row_that_does_not_match_its_hash(self, db_session):
        """The trigger is not the only way a row can arrive wrong.

        A restore from a bad backup, a migration that rewrote JSON, a superuser
        with the trigger disabled - none of those go through an UPDATE this
        process can see. So the row is INSERTed already inconsistent, which is
        what those look like from here.

        Worth recording how this test got written: the first version mutated
        the ORM object and let SQLAlchemy flush it. The trigger rejected the
        UPDATE, which is the trigger working - but it meant the hash check was
        never reached. The two defences are independent and this one needs its
        own path to the failure.
        """
        hub = await _hub(db_session)
        rogue_id = uuid.uuid4()
        await db_session.execute(
            text(
                """
                INSERT INTO decision_snapshots
                    (id, hub_id, decided_at, mode, engine, inputs, assignments,
                     unassigned_stop_ids, inputs_hash, plan_duration_seconds,
                     hub_closed, stop_count, driver_count, assigned_count)
                VALUES
                    (:id, :hub, :at, 'live', 'stub', '{"released_order_ids":["order-invented"]}',
                     '[]', '[]', :wrong_hash, 0.01, false, 0, 0, 0)
                """
            ),
            {
                "id": rogue_id,
                "hub": hub.id,
                "at": DECIDED_AT,
                # A hash of something this row does not contain.
                "wrong_hash": canonical_inputs_hash({"released_order_ids": ["order-real"]}),
            },
        )

        with pytest.raises(ValueError, match="altered since it was written"):
            await replay_inputs(db_session, rogue_id)


class TestTheHash:
    def test_two_cycles_that_saw_the_same_world_hash_alike(self):
        """What the digest is for: comparing cycles without diffing JSON."""
        hub_id = str(uuid.uuid4())
        from app.record import freeze_plan_inputs

        assert canonical_inputs_hash(freeze_plan_inputs(_plan(hub_id))) == (
            canonical_inputs_hash(freeze_plan_inputs(_plan(hub_id)))
        )

    def test_a_different_world_hashes_differently(self):
        from app.record import freeze_plan_inputs

        hub_id = str(uuid.uuid4())
        one = canonical_inputs_hash(freeze_plan_inputs(_plan(hub_id, drivers=1)))
        two = canonical_inputs_hash(freeze_plan_inputs(_plan(hub_id, drivers=2)))
        assert one != two

    def test_query_order_does_not_change_the_hash(self):
        """Stops and drivers are sorted before hashing. Without that the digest
        would be a nonce and the comparison it exists for would never match."""
        from app.record import freeze_plan_inputs

        hub_id = str(uuid.uuid4())
        plan = _plan(hub_id, stops=3, drivers=2)
        expected = canonical_inputs_hash(freeze_plan_inputs(plan))

        plan.stops.reverse()
        plan.drivers.reverse()
        assert canonical_inputs_hash(freeze_plan_inputs(plan)) == expected


class TestModes:
    async def test_a_shadow_decision_is_recorded_like_a_live_one(self, db_session):
        """W9 compares the two, so both sides need their inputs."""
        hub = await _hub(db_session)
        await record_decision(db_session, _plan(str(hub.id)), mode=MODE_SHADOW)
        await record_decision(db_session, _plan(str(hub.id)), mode=MODE_LIVE)

        rows = list(await db_session.scalars(select(DecisionSnapshot)))
        assert {r.mode for r in rows} == {MODE_SHADOW, MODE_LIVE}
        # Same world, so the same digest - which is what makes a divergence
        # between the two a real disagreement rather than a data difference.
        assert len({r.inputs_hash for r in rows}) == 1

    async def test_an_unknown_mode_is_refused(self, db_session):
        hub = await _hub(db_session)
        with pytest.raises(ValueError, match="mode must be"):
            await record_decision(db_session, _plan(str(hub.id)), mode="maybe")

    async def test_the_counts_come_from_the_inputs_they_describe(self, db_session):
        hub = await _hub(db_session)
        snapshot = await record_decision(db_session, _plan(str(hub.id), stops=4, drivers=3))

        assert snapshot.stop_count == len(snapshot.inputs["stops"]) == 4
        assert snapshot.driver_count == len(snapshot.inputs["drivers"]) == 3

    async def test_decided_at_is_the_planners_clock_not_the_writers(self, db_session):
        """A slow commit must not move when the decision happened."""
        hub = await _hub(db_session)
        snapshot = await record_decision(db_session, _plan(str(hub.id)))

        assert snapshot.decided_at == DECIDED_AT
        assert snapshot.decided_at < datetime.now(timezone.utc) - timedelta(seconds=1)
