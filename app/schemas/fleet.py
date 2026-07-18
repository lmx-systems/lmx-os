from pydantic import BaseModel


class DriverLocation(BaseModel):
    driver_id: str
    lat: float
    lng: float
    recorded_at: str  # ISO timestamp, kept as str to avoid tz round-trip cost on hot path


class DriverState(BaseModel):
    driver_id: str
    hub_id: str
    status: str  # off_shift | available | en_route | on_break
    capacity_units: int
    load_units: float = 0
    current_route_id: str | None = None
    # Not stored in Redis (FleetStateManager never reads/writes this field -
    # see its docstring) - populated only by GET /fleet/{hub_id}/drivers via
    # a batch Postgres lookup, since Driver.name lives in Postgres, not the
    # Redis fleet-state hash the optimizer's hot path reads.
    name: str | None = None
