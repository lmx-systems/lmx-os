from pydantic import BaseModel


class DriverLocation(BaseModel):
    driver_id: str
    lat: float
    lng: float
    recorded_at: str  # ISO timestamp, kept as str to avoid tz round-trip cost on hot path


class DriverState(BaseModel):
    driver_id: str
    hub_id: str
    status: str  # off_shift | available | offered | en_route | on_break
    capacity_units: int
    load_units: float = 0
    current_route_id: str | None = None
    # Not stored in Redis (FleetStateManager never reads/writes this field -
    # see its docstring) - populated only by GET /fleet/{hub_id}/drivers via
    # a batch Postgres lookup, since Driver.name lives in Postgres, not the
    # Redis fleet-state hash the optimizer's hot path reads.
    name: str | None = None

    # Last reported position (docs/ROADMAP.md F1), for the ops fleet view.
    # Same "enriched on the way out, never round-tripped through Redis's
    # state hash" treatment as `name` above, but for a different reason:
    # location DOES live in Redis, just under its own per-driver key
    # (fleet:{hub}:driver:{id}:location) rather than in the state hash, so
    # populating it costs one extra read per driver. The optimizer already
    # fetches it directly via get_driver_location and has no use for it
    # here, so this stays off the hot path.
    #
    # All three are null together when a driver has never reported a
    # position - which is every driver until the app starts pinging, and is
    # exactly the state that makes the optimizer skip them.
    lat: float | None = None
    lng: float | None = None
    location_recorded_at: str | None = None
