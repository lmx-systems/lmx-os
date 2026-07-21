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

    # Inbound-webhook signature verification (app/api/webhooks.py,
    # app/messaging/twilio_signature.py) needs the exact public URL Twilio
    # was configured to call, scheme+host included - `request.url` as this
    # app sees it is correct only when nothing sits in front of it. Behind
    # a future reverse proxy/load balancer (Phase 5's hosting decision),
    # set this to the real public base URL (e.g.
    # "https://api.lmxit.com") so the scheme/host used in the signature
    # computation matches what Twilio actually signed, not this
    # container's internal view of the request. Unset = use request.url
    # as-is, correct for today's un-proxied docker-compose deployment.
    twilio_webhook_base_url: str | None = None

    # Driver app Phase 3 (screens 1p/1q): where a driver's "contact
    # support" message actually goes. Unset = the message is still stored
    # (app/models/message.py) so it's not silently lost, but no SMS send
    # is attempted - there's nowhere real to send it to yet. Same
    # "unconfigured -> stub/store-only" pattern as everything else in this
    # file with no credentials yet.
    support_phone_number: str | None = None

    epicor_base_url: str | None = None
    epicor_api_key: str | None = None

    # Payroll (app/payroll/): W2 hours submission today, with 1099/gig pay
    # rails expected to join behind the same PayrollProvider interface as
    # the driver-classification phases roll out (docs/NEXT_STEPS.md). No
    # Rippling account is provisioned yet - unset means every submission
    # runs through StubPayrollProvider instead.
    rippling_api_key: str | None = None
    rippling_base_url: str | None = None

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

    # Driver app (Phase 1, see docs/NEXT_STEPS.md item 12): real per-driver
    # auth, unlike api_shared_secret above. Signs/verifies the JWT issued on
    # OTP verification (app/driver_auth/tokens.py). The default is an
    # obviously-fake dev value, not a generated secret - deliberately loud
    # (see app/driver_auth/tokens.py's startup check) rather than silently
    # "secure-looking" in an environment nobody configured it for.
    driver_jwt_secret: str = "dev-only-insecure-secret-change-in-production"
    driver_jwt_expiry_hours: int = 24 * 30  # drivers stay logged in ~a month

    # How long a driver has to accept/decline a job offer before it expires
    # and the order goes back to the hold queue for reassignment (see
    # app/optimizer/service.py / app/models/route_offer.py).
    job_offer_ttl_seconds: int = 120

    # Client portal (Phase 8, see docs/ROADMAP.md) - password-based JWT for
    # Client.portal_email/portal_password_hash logins (app/client_auth/).
    # Deliberately a separate secret from driver_jwt_secret: a client token
    # and a driver token must never be interchangeable even if one secret
    # were ever compromised, since they authorize very different things
    # (a client's own order history/billing vs. a driver's active route).
    client_jwt_secret: str = "dev-only-insecure-secret-change-in-production"
    client_jwt_expiry_hours: int = 24 * 7  # shorter-lived than a driver's month; re-login weekly

    # Minimal client onboarding (Phase 8): gates POST /admin/clients behind
    # the existing internal ops shared secret (api_shared_secret above),
    # not a new auth scheme - this is an internal/admin tool, not a
    # client-facing or driver-facing surface.

    @property
    def dashboard_cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.dashboard_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def assert_jwt_secrets_are_distinct() -> None:
    """Fail fast at boot, alongside assert_client_jwt_secret_configured()/
    assert_driver_jwt_secret_configured(), if a real deployment ever ends up
    with CLIENT_JWT_SECRET and DRIVER_JWT_SECRET set to the same value.

    Each of those two checks only catches its own field falling back to the
    published dev-only default - neither one notices if an operator instead
    configures both env vars to one shared real secret. That would silently
    defeat client_jwt_secret's whole reason for existing (see its docstring
    above): a client token would decode successfully as a driver token and
    vice versa, exactly the interchangeability this system is designed to
    prevent. Both settings sharing the *default* value in development is
    expected and fine - that path is already refused by the other two checks
    outside development, so this one only needs to fire once a real,
    non-default secret has been configured for both.
    """
    if (
        settings.environment != "development"
        and settings.client_jwt_secret == settings.driver_jwt_secret
    ):
        raise RuntimeError(
            "CLIENT_JWT_SECRET and DRIVER_JWT_SECRET are set to the same value - "
            "refusing to start. A client portal session token must never be "
            "valid as a driver session token (or vice versa); configure two "
            "distinct secrets."
        )
