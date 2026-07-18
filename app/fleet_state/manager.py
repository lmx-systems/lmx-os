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

import structlog

from app.redis_client import get_client, timed_operation
from app.schemas.fleet import DriverLocation, DriverState

logger = structlog.get_logger(__name__)


def _state_key(hub_id: str, driver_id: str) -> str:
    return f"fleet:{hub_id}:driver:{driver_id}:state"


def _location_key(hub_id: str, driver_id: str) -> str:
    return f"fleet:{hub_id}:driver:{driver_id}:location"


def _available_set_key(hub_id: str) -> str:
    return f"fleet:{hub_id}:available_drivers"


def _all_drivers_set_key(hub_id: str) -> str:
    return f"fleet:{hub_id}:all_drivers"


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

    async def get_driver_state(self, hub_id: str, driver_id: str) -> DriverState | None:
        async with timed_operation("fleet.get_driver_state"):
            data = await self._redis.hgetall(_state_key(hub_id, driver_id))
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

    async def get_driver_location(self, hub_id: str, driver_id: str) -> DriverLocation | None:
        async with timed_operation("fleet.get_driver_location"):
            data = await self._redis.hgetall(_location_key(hub_id, driver_id))
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
        return list(members)

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
        return list(members)

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
        for driver_id, data in zip(driver_ids, results, strict=True):
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
