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
    #
    # Route Optimization API (unlike the other Maps Platform APIs) is a
    # Cloud IAM-gated API, not an API-key product: it authenticates via
    # Application Default Credentials (a service account JSON at
    # GOOGLE_APPLICATION_CREDENTIALS, workload identity, etc.) scoped to
    # `cloud-platform`. All this needs from us is which project to bill/
    # authorize against.
    google_cloud_project_id: str | None = None
    google_maps_api_key: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None

    epicor_base_url: str | None = None
    epicor_api_key: str | None = None

    # Origins allowed to call the API cross-origin - the orchestrator
    # dashboard (dashboard/) in dev, and whatever the dashboard is actually
    # deployed at in production. NOT a substitute for real authentication -
    # see docs/ARCHITECTURE.md's auth caveat. Comma-separated in the env var.
    dashboard_cors_origins: str = "http://localhost:5173"

    # Interim stopgap for docs/ARCHITECTURE.md's "Recommended next steps"
    # item 0: every endpoint (bar /health and API docs) requires this value
    # in an X-API-Key header. Unset (the default) leaves the API open, same
    # as before this existed - fine for local dev, not for anything
    # reachable beyond localhost. This is a shared secret, not per-user
    # auth; a client-facing dashboard or driver app needs the real thing.
    api_shared_secret: str | None = None

    @property
    def dashboard_cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.dashboard_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
