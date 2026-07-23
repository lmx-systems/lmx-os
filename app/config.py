"""
Central configuration. Loaded once as a singleton `settings` object.

All tunables that Section 6-12 of LMX_OS_Technical_Design_2.md call out as
environment-specific (cluster radius, optimizer budget, third-party creds)
live here so they're never hardcoded in business logic.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.secrets_provider import load_secrets_into_environment

# Must run before Settings is ever constructed below - see
# app/secrets_provider.py's module docstring for why this specific
# placement (top of this module, before the class body) is what makes a
# real vault's values actually take effect with zero other code changes.
load_secrets_into_environment()


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

    # Push notifications for new job offers (docs/ROADMAP.md A1,
    # app/messaging/push_client.py). Unlike Twilio/Rippling, Expo's push
    # service needs no account/credential to call in the basic case - the
    # real gap is client-side: the driver app has no EAS project id
    # configured yet (see driver-app/app.json), which
    # Notifications.getExpoPushTokenAsync() requires to mint a real push
    # token, so no device can register one regardless of this flag today.
    # Defaults to disabled (not credential-gated, since there's no
    # credential to gate on) so a real send is never attempted before
    # that's deliberately turned on.
    expo_push_enabled: bool = False
    # Optional - Expo's "enhanced security" mode. Unset is a fully valid,
    # working configuration; only needed if that mode is turned on for the
    # Expo project later.
    expo_push_access_token: str | None = None

    # Real proof-of-delivery photo/signature capture and parcel-scan
    # barcode images (docs/ROADMAP.md A2/A3, app/storage/photo_upload_client.py)
    # upload to S3 via a presigned PUT URL the driver app requests just
    # before capturing. Unset bucket = same "unconfigured -> stub" status
    # as Twilio/Rippling/Expo push - the stub issues a local marker URL
    # (unchanged from this app's original local-capture:// placeholder
    # shape) instead of a real presigned one, so the rest of the capture
    # flow is fully buildable/testable without a real AWS account. Uses
    # boto3's own default credential chain (env vars/IAM role), same as
    # app/secrets_provider.py's AWSSecretsManagerProvider - no separate
    # access-key settings here on purpose.
    photo_upload_bucket: str | None = None
    photo_upload_region: str = "us-east-1"

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
    #
    # Also doubles as the base for the *outbound* URLs masked voice
    # calling hands Twilio (docs/ROADMAP.md A7, app/api/driver_routes.py's
    # call_customer) - Twilio needs somewhere public to call back into for
    # the connect-TwiML and call-status webhooks, and this is the same
    # "our real public address" value either direction needs.
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

    # Observability (docs/ROADMAP.md S4) - error tracking via Sentry
    # (app/logging_config.py). Unset = sentry_sdk.init() is never called
    # at all, so every sentry_sdk.capture_*() call becomes an
    # already-safe no-op (the SDK's own behavior with no client
    # configured) - same "unconfigured credential -> stub" status as
    # Twilio/Rippling elsewhere in this file. traces_sample_rate defaults
    # to 0 (no performance-monitoring transactions sent, error tracking
    # only) - deliberately conservative until there's a real account to
    # judge event-volume cost against.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0

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

    # General per-IP API rate limiting (app/rate_limit.py) - deliberately
    # generous, see that module's docstring for why. There's no
    # "0 = disabled" escape hatch - a real deployment should never want
    # zero rate limiting, only a tuned cap.
    general_rate_limit_max_requests: int = 600
    general_rate_limit_window_seconds: int = 60

    # Driver app (Phase 1, see docs/NEXT_STEPS.md item 12): real per-driver
    # auth. Signs/verifies the JWT issued on OTP verification
    # (app/driver_auth/tokens.py). The default is an
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

    # Ops dashboard (docs/ROADMAP.md S1) - password-based JWT for
    # OpsUser.email/password_hash logins (app/ops_auth/), replacing the
    # shared X-API-Key stopgap this file used to have. Deliberately a
    # separate secret from client/driver_jwt_secret for the same
    # non-interchangeability reason as those two - an ops session token
    # authorizes fleet-wide read/write across every hub, which must never
    # be satisfiable by a client or driver token even if one secret were
    # ever compromised.
    ops_jwt_secret: str = "dev-only-insecure-secret-change-in-production"
    ops_jwt_expiry_hours: int = 24 * 7  # same cadence as client portal - weekly re-login

    @property
    def dashboard_cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.dashboard_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def assert_jwt_secrets_are_distinct() -> None:
    """Fail fast at boot, alongside assert_client_jwt_secret_configured()/
    assert_driver_jwt_secret_configured()/assert_ops_jwt_secret_configured(),
    if a real deployment ever ends up with two of CLIENT_JWT_SECRET/
    DRIVER_JWT_SECRET/OPS_JWT_SECRET set to the same value.

    Each of those three checks only catches its own field falling back to
    the published dev-only default - none of them notices if an operator
    instead configures two of the three env vars to one shared real
    secret. That would silently defeat the whole reason a separate secret
    exists per token type: a token issued for one would decode
    successfully as another, exactly the interchangeability this system
    is designed to prevent - most severely for ops (a session that
    authorizes fleet-wide read/write across every hub) being satisfiable
    by a client or driver token. All three sharing the *default* value in
    development is expected and fine - that path is already refused by
    the other three checks outside development, so this one only needs
    to fire once a real, non-default secret has been configured for two
    (or all three) of them.
    """
    if settings.environment == "development":
        return
    secrets_by_name = {
        "CLIENT_JWT_SECRET": settings.client_jwt_secret,
        "DRIVER_JWT_SECRET": settings.driver_jwt_secret,
        "OPS_JWT_SECRET": settings.ops_jwt_secret,
    }
    names = list(secrets_by_name)
    for i, name_a in enumerate(names):
        for name_b in names[i + 1 :]:
            if secrets_by_name[name_a] == secrets_by_name[name_b]:
                raise RuntimeError(
                    f"{name_a} and {name_b} are set to the same value - refusing to "
                    "start. A session token for one must never be valid as another "
                    "(or vice versa); configure distinct secrets for all three."
                )
