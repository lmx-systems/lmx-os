"""
Central configuration. Loaded once as a singleton `settings` object.

All tunables that Section 6-12 of LMX_OS_Technical_Design_2.md call out as
environment-specific (cluster radius, optimizer budget, third-party creds)
live here so they're never hardcoded in business logic.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    # Postgres
    database_url: str = "postgresql+asyncpg://lmx:change_me@localhost:5432/lmx_os"

    # Redis - fleet state / hold queue. Design doc requires <50ms reads on
    # every re-optimization pass, so this is a dedicated pool, not shared
    # with anything else.
    redis_url: str = "redis://localhost:6379/0"

    # Batch-hold queue (Section 8): default clustering radius in miles.
    batch_hold_cluster_radius_miles: float = 0.8

    # Dispatch optimizer cycle budget in seconds (Section 9 performance
    # target: <5s for a hub with up to 20 drivers / 100 open orders).
    optimizer_cycle_budget_seconds: float = 5.0

    # Third-party integrations - all optional at this phase. Absence of a
    # key means the corresponding client runs in stub/mock mode so the
    # rest of the system is still runnable and testable end-to-end.
    google_routes_api_key: str | None = None
    google_maps_api_key: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None

    epicor_base_url: str | None = None
    epicor_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
