import os
from pathlib import Path
from urllib.parse import quote

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _compose_credentials() -> tuple[str, str]:
    """The user and password `docker compose up` will give the local Postgres.

    `docker-compose.yml` substitutes `POSTGRES_USER`/`POSTGRES_PASSWORD` from
    `.env`, so a developer who starts the services gets a server whose password is
    whatever their own `.env` says - not the literal "test" this file used to
    assume. The two disagreed, the integration fixtures could not connect, and
    909 of 1175 tests skipped while pytest still exited 0.

    **The database name is deliberately not read from `.env`.** That file's
    `POSTGRES_DB` is the *development* database, and the integration fixtures drop
    and recreate the public schema of whatever they are pointed at. Taking the name
    from there would wipe a developer's local data the first time they ran the
    suite. Only the credentials come from `.env`; the database is always
    `lmx_os_test`.

    Falls back to docker-compose.yml's own defaults when there is no `.env`, so a
    fresh clone still lands on the right server.
    """
    user, password = "lmx", "change_me"

    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return user, password

    try:
        for raw in env_file.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if not value:
                continue
            if key.strip() == "POSTGRES_USER":
                user = value
            elif key.strip() == "POSTGRES_PASSWORD":
                password = value
    except OSError:
        # An unreadable .env is not a reason to fail collection - fall back to the
        # compose defaults and let the connection attempt report the real problem.
        pass

    return user, password


def _default_database_url() -> str:
    user, password = _compose_credentials()
    return (
        f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}"
        "@localhost:5432/lmx_os_test"
    )


# Point every test run at throwaway local connection strings before any
# app module (which reads settings at import time) gets imported. Tests
# never actually open these connections unless a test explicitly opts in.
#
# `setdefault`, so an explicit DATABASE_URL always wins - CI sets its own
# (see .github/workflows/ci.yml) and is unaffected by the derivation above.
os.environ.setdefault("DATABASE_URL", _default_database_url())
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("ENVIRONMENT", "test")


PHOTO_BUCKET = "lmx-photo-uploads"
PHOTO_REGION = "us-east-1"


@pytest.fixture
def photo_bucket(monkeypatch, tmp_path) -> str:
    """Photos and documents stored in a private S3 bucket, signed with fake keys.

    Presigning is local arithmetic with no network call, so a real boto3 client
    with fake keys produces exactly the links production would. Nothing from this
    machine's own AWS setup is read. Returns the bucket's object URL prefix.
    """
    import boto3

    from app.config import settings

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-secret")
    for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-aws-credentials"))
    # boto3 caches credentials on its default session; start a fresh one.
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)
    monkeypatch.setattr(settings, "photo_upload_bucket", PHOTO_BUCKET)
    monkeypatch.setattr(settings, "photo_upload_region", PHOTO_REGION)
    return f"https://{PHOTO_BUCKET}.s3.{PHOTO_REGION}.amazonaws.com/"


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Say loudly, at the end, when the integration suite did not run.

    pytest's own output buries this: 909 skips render as a quiet count beside the
    passes, and the exit code is 0 either way. Someone who runs `docker compose up`
    and then `pytest` sees green and reasonably concludes the database-backed
    behaviour was covered, when 77% of the suite never executed.

    CI catches this separately by grepping for the skip message, and that check
    stays - this is the local half of the same guard.
    """
    reason = getattr(config, "lmx_integration_skip_reason", None)
    if not reason:
        return

    write = terminalreporter.write_line
    terminalreporter.section("integration tests did NOT run", sep="=", red=True, bold=True)
    write(reason)
    write("")
    write("Everything database-backed was skipped: ingestion, billing, credits, COD,")
    write("webhooks and the order API. A green result here does not cover them.")
    write("")
    write("To run them:  docker compose up -d postgres redis")
    write("To make this a failure instead of a warning:  LMX_REQUIRE_INTEGRATION=1 pytest")
