"""
Fleet State Manager (component 4 in the design doc).

Owns the live, per-hub view of driver availability/location/capacity in
Redis. This is deliberately NOT backed by Postgres on the read path - the
Dispatch Optimizer re-reads this every cycle and the design doc's hard
requirement is <50ms per read, which Postgres round-trips can't reliably
hit under load. Postgres (drivers table) remains the system of record for
driver identity/config; this class is the fast-changing runtime state.

Key layout (all scoped per hub so a hub outage/reset can't affect others):
  fleet:{hub_id}:driver:{driver_id}:state     hash  - status, capacity_units, load_units, current_route_id
  fleet:{hub_id}:driver:{driver_id}:location  hash  - lat, lng, recorded_at
  fleet:{hub_id}:available_drivers            set   - driver_ids currently status=available
  fleet:{hub_id}:all_drivers                  set   - every driver_id ever upserted for this hub,
                                                       regardless of current status (dashboard/
                                                       overview use only - the optimizer's hot path
                                                       only ever reads available_drivers)
"""
from __future__ import annotations

from collections.abc import Mapping

import structlog

from app.redis_client import as_text, get_client, timed_operation
from app.schemas.fleet import DriverLocation, DriverState

logger = structlog.get_logger(__name__)


def _state_key(hub_id: str, driver_id: str) -> str:
    return f"fleet:{hub_id}:driver:{driver_id}:state"


def _location_key(hub_id: str, driver_id: str) -> str:
    return f"fleet:{hub_id}:driver:{driver_id}:location"


def _available_set_key(hub_id: str) -> str:
    return f"fleet:{hub_id}:available_drivers"


def _fields(reply: Mapping[bytes | str, bytes | str]) -> dict[str, str]:
    """A hash reply with text field names as well as text values.

    The names matter as much as the values: from a client that does not decode,
    `data["status"]` is a KeyError, because the field came back as `b"status"`.
    """
    return {as_text(name): as_text(value) for name, value in reply.items()}


def _all_drivers_set_key(hub_id: str) -> str:
    return f"fleet:{hub_id}:all_drivers"


# KEYS: state, available set, all-drivers set.
# ARGV: driver_id, status, capacity_units, load_units, current_route_id.
_SEED_IF_ABSENT = """
if redis.call('exists', KEYS[1]) == 1 then
    return 0
end
redis.call('hset', KEYS[1], 'status', ARGV[2], 'capacity_units', ARGV[3],
           'load_units', ARGV[4], 'current_route_id', ARGV[5])
if ARGV[2] == 'available' then
    redis.call('sadd', KEYS[2], ARGV[1])
else
    redis.call('srem', KEYS[2], ARGV[1])
end
redis.call('sadd', KEYS[3], ARGV[1])
return 1
"""

_SEED_LOCATION_IF_ABSENT = """
if redis.call('exists', KEYS[1]) == 1 then
    return 0
end
redis.call('hset', KEYS[1], 'lat', ARGV[1], 'lng', ARGV[2], 'recorded_at', ARGV[3])
return 1
"""

# KEYS: state, available set. ARGV: driver_id, expected status, new status.
_CORRECT_STATUS_IF = """
if redis.call('hget', KEYS[1], 'status') ~= ARGV[2] then
    return 0
end
-- Off a route means carrying nothing.
redis.call('hset', KEYS[1], 'status', ARGV[3], 'current_route_id', '', 'load_units', 0)
if ARGV[3] == 'available' then
    redis.call('sadd', KEYS[2], ARGV[1])
else
    redis.call('srem', KEYS[2], ARGV[1])
end
return 1
"""


class FleetStateManager:
    def __init__(self) -> None:
        self._redis = get_client()

    async def upsert_driver_state(self, state: DriverState) -> None:
        async with timed_operation("fleet.upsert_driver_state"):
            key = _state_key(state.hub_id, state.driver_id)
            pipe = self._redis.pipeline(transaction=True)
            pipe.hset(
                key,
                mapping={
                    "status": state.status,
                    "capacity_units": state.capacity_units,
                    "load_units": state.load_units,
                    "current_route_id": state.current_route_id or "",
                },
            )
            available_key = _available_set_key(state.hub_id)
            if state.status == "available":
                pipe.sadd(available_key, state.driver_id)
            else:
                pipe.srem(available_key, state.driver_id)
            pipe.sadd(_all_drivers_set_key(state.hub_id), state.driver_id)
            await pipe.execute()

    async def seed_driver_state_if_absent(self, state: DriverState) -> bool:
        """Write a driver's state only if Redis has none; True if it was written.

        For the rebuild from Postgres (app/optimizer/redis_rebuild.py). One
        script, so a state the driver's own app writes between the check and the
        write can't be overwritten by the rebuild's older picture.
        """
        async with timed_operation("fleet.seed_driver_state_if_absent"):
            written = await self._redis.eval(
                _SEED_IF_ABSENT,
                3,
                _state_key(state.hub_id, state.driver_id),
                _available_set_key(state.hub_id),
                _all_drivers_set_key(state.hub_id),
                state.driver_id,
                state.status,
                state.capacity_units,
                state.load_units,
                state.current_route_id or "",
            )
        return bool(written)

    async def correct_status_if(
        self, hub_id: str, driver_id: str, *, expect_status: str, new_status: str
    ) -> bool:
        """Set a driver's status only if it is still `expect_status`, clearing the
        route; True if it changed. For drift the rebuild proves from Postgres."""
        async with timed_operation("fleet.correct_status_if"):
            changed = await self._redis.eval(
                _CORRECT_STATUS_IF,
                2,
                _state_key(hub_id, driver_id),
                _available_set_key(hub_id),
                driver_id,
                expect_status,
                new_status,
            )
        return bool(changed)

    async def get_driver_state(self, hub_id: str, driver_id: str) -> DriverState | None:
        async with timed_operation("fleet.get_driver_state"):
            data = _fields(await self._redis.hgetall(_state_key(hub_id, driver_id)))
        if not data:
            return None
        return DriverState(
            driver_id=driver_id,
            hub_id=hub_id,
            status=data["status"],
            capacity_units=int(data["capacity_units"]),
            load_units=float(data["load_units"]),
            current_route_id=data["current_route_id"] or None,
        )

    async def update_driver_location(self, location: DriverLocation, hub_id: str) -> None:
        async with timed_operation("fleet.update_driver_location"):
            await self._redis.hset(
                _location_key(hub_id, location.driver_id),
                mapping={
                    "lat": location.lat,
                    "lng": location.lng,
                    "recorded_at": location.recorded_at,
                },
            )

    async def seed_location_if_absent(self, location: DriverLocation, hub_id: str) -> bool:
        """Write a position only if Redis has none, so a ping that lands during the
        rebuild is never replaced by the older one Postgres had."""
        async with timed_operation("fleet.seed_location_if_absent"):
            written = await self._redis.eval(
                _SEED_LOCATION_IF_ABSENT,
                1,
                _location_key(hub_id, location.driver_id),
                location.lat,
                location.lng,
                location.recorded_at,
            )
        return bool(written)

    async def get_driver_location(self, hub_id: str, driver_id: str) -> DriverLocation | None:
        async with timed_operation("fleet.get_driver_location"):
            data = _fields(await self._redis.hgetall(_location_key(hub_id, driver_id)))
        if not data:
            return None
        return DriverLocation(
            driver_id=driver_id,
            lat=float(data["lat"]),
            lng=float(data["lng"]),
            recorded_at=data["recorded_at"],
        )

    async def get_available_driver_ids(self, hub_id: str) -> list[str]:
        """
        Single Redis SMEMBERS call - this is the read the Dispatch Optimizer
        hits every cycle to know which drivers it can assign stops to.
        """
        async with timed_operation("fleet.get_available_driver_ids"):
            members = await self._redis.smembers(_available_set_key(hub_id))
        return [as_text(member) for member in members]

    async def get_fleet_snapshot(self, hub_id: str) -> list[DriverState]:
        """
        Bulk read of every *available* driver's state for a hub in one
        pipelined round trip - this is what the Dispatch Optimizer calls on
        its hot path every cycle. For a full roster including off-shift/
        en-route drivers (dashboards, not the optimizer), use
        get_fleet_overview instead.
        """
        driver_ids = await self.get_available_driver_ids(hub_id)
        return await self._bulk_read_states(hub_id, driver_ids)

    async def get_all_driver_ids(self, hub_id: str) -> list[str]:
        """Every driver ever upserted for this hub, regardless of current status."""
        async with timed_operation("fleet.get_all_driver_ids"):
            members = await self._redis.smembers(_all_drivers_set_key(hub_id))
        return [as_text(member) for member in members]

    async def get_fleet_overview(self, hub_id: str) -> list[DriverState]:
        """
        Full roster for a hub - available, en_route, on_break, and
        off_shift drivers alike. Not on the optimizer's hot path; this is
        for the orchestrator dashboard, so a driver going off-shift doesn't
        just disappear from view.
        """
        driver_ids = await self.get_all_driver_ids(hub_id)
        return await self._bulk_read_states(hub_id, driver_ids)

    async def _bulk_read_states(self, hub_id: str, driver_ids: list[str]) -> list[DriverState]:
        if not driver_ids:
            return []
        async with timed_operation("fleet.bulk_read_states"):
            pipe = self._redis.pipeline(transaction=False)
            for driver_id in driver_ids:
                pipe.hgetall(_state_key(hub_id, driver_id))
            results = await pipe.execute()

        states: list[DriverState] = []
        for driver_id, reply in zip(driver_ids, results, strict=True):
            data = _fields(reply)
            if not data:
                continue
            states.append(
                DriverState(
                    driver_id=driver_id,
                    hub_id=hub_id,
                    status=data["status"],
                    capacity_units=int(data["capacity_units"]),
                    load_units=float(data["load_units"]),
                    current_route_id=data["current_route_id"] or None,
                )
            )
        return states
