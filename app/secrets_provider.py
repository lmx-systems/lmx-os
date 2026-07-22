"""
Secrets management (docs/ROADMAP.md S2) - every credential (DB password,
JWT secrets, third-party API keys) lives in a plain .env file today. This
is the extension point for a real vault, without committing this
codebase to one before an operator has actually picked it - same
"unconfigured credential -> stub" shape as Twilio/Rippling/Sentry
elsewhere in this app, just one level up (this is about how the app's
*own* configuration gets loaded, not a third-party integration the app
calls out to at request time).

  - EnvSecretsProvider is today's actual behavior: nothing to fetch,
    os.environ (already populated by the shell/.env/Docker/whatever
    deployment platform) is already the source of truth.
  - AWSSecretsManagerProvider is a real implementation, unexercised
    without a real AWS account/secret configured - same status as this
    codebase's other "implemented, not yet verified live" clients (e.g.
    app/optimizer/google_routes_client.py). boto3 is imported lazily and
    deliberately NOT added to requirements.txt - it's a real dependency
    only once this specific provider is actually chosen and configured,
    not before; `pip install boto3` first if that day comes.

load_secrets_into_environment() runs at the very top of app/config.py,
before Settings is even defined - so if a real vault IS configured
(SECRETS_MANAGER_SECRET_ID set), its values land in os.environ before
pydantic-settings ever constructs Settings from it, and take precedence
over anything in .env (env vars already outrank the .env file in
pydantic-settings' own default source ordering) with zero changes needed
anywhere else - every existing `settings.foo` read stays exactly as it
is. An operator-set env var is never overwritten by a vault value
(os.environ.setdefault, not direct assignment) - the vault fills gaps,
it doesn't override an explicit local choice.

Real, honest gap this doesn't solve: which vault to actually adopt, when
to migrate, and how secret rotation should work operationally are all
still open, deployment-platform-specific decisions (the same nature as
docs/ROADMAP.md S3's hosting decision) - this is the code-side extension
point, not that decision.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod


class SecretsProvider(ABC):
    @abstractmethod
    def get_all_secrets(self) -> dict[str, str]:
        """Every secret this provider knows about, name -> value."""
        raise NotImplementedError


class EnvSecretsProvider(SecretsProvider):
    def get_all_secrets(self) -> dict[str, str]:
        return {}


class AWSSecretsManagerProvider(SecretsProvider):
    """Fetches one JSON-object secret (name -> value pairs, e.g.
    {"DRIVER_JWT_SECRET": "...", "DATABASE_URL": "..."}) from AWS Secrets
    Manager."""

    def __init__(self, secret_id: str, region_name: str) -> None:
        self._secret_id = secret_id
        self._region_name = region_name

    def get_all_secrets(self) -> dict[str, str]:
        import boto3

        client = boto3.client("secretsmanager", region_name=self._region_name)
        response = client.get_secret_value(SecretId=self._secret_id)
        return json.loads(response["SecretString"])


def get_secrets_provider() -> SecretsProvider:
    secret_id = os.environ.get("SECRETS_MANAGER_SECRET_ID")
    if secret_id:
        region = os.environ.get("AWS_REGION", "us-east-1")
        return AWSSecretsManagerProvider(secret_id=secret_id, region_name=region)
    return EnvSecretsProvider()


def load_secrets_into_environment() -> None:
    for name, value in get_secrets_provider().get_all_secrets().items():
        os.environ.setdefault(name, value)
