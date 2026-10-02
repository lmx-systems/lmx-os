"""
app/secrets_provider.py - the extension point for a real vault
(docs/ROADMAP.md S2). Most of these inject a fake boto3 into sys.modules,
which isolates the provider's logic but cannot notice whether boto3 is
installed at all - and it wasn't, until infra/aws chose this provider. The
last test drives the real client, stubbed at the wire.
"""
import os
import sys
from unittest.mock import MagicMock

from app.secrets_provider import (
    AWSSecretsManagerProvider,
    EnvSecretsProvider,
    get_secrets_provider,
    load_secrets_into_environment,
)


def test_env_secrets_provider_has_nothing_to_fetch():
    assert EnvSecretsProvider().get_all_secrets() == {}


def test_get_secrets_provider_defaults_to_env_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SECRETS_MANAGER_SECRET_ID", raising=False)
    assert isinstance(get_secrets_provider(), EnvSecretsProvider)


def test_get_secrets_provider_uses_aws_when_configured(monkeypatch):
    monkeypatch.setenv("SECRETS_MANAGER_SECRET_ID", "arn:aws:secretsmanager:fake")
    provider = get_secrets_provider()
    assert isinstance(provider, AWSSecretsManagerProvider)
    assert provider._secret_id == "arn:aws:secretsmanager:fake"


def test_load_secrets_into_environment_sets_a_new_value(monkeypatch):
    monkeypatch.delenv("MY_TEST_SECRET_XYZ", raising=False)
    fake_provider = MagicMock()
    fake_provider.get_all_secrets.return_value = {"MY_TEST_SECRET_XYZ": "vault-value"}
    monkeypatch.setattr("app.secrets_provider.get_secrets_provider", lambda: fake_provider)

    load_secrets_into_environment()

    assert os.environ["MY_TEST_SECRET_XYZ"] == "vault-value"


def test_load_secrets_into_environment_never_overrides_an_explicit_env_var(monkeypatch):
    monkeypatch.setenv("MY_TEST_SECRET_XYZ", "explicit-value")
    fake_provider = MagicMock()
    fake_provider.get_all_secrets.return_value = {"MY_TEST_SECRET_XYZ": "vault-value"}
    monkeypatch.setattr("app.secrets_provider.get_secrets_provider", lambda: fake_provider)

    load_secrets_into_environment()

    assert os.environ["MY_TEST_SECRET_XYZ"] == "explicit-value"


def test_aws_secrets_manager_provider_parses_the_json_secret(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_secret_value.return_value = {"SecretString": '{"DRIVER_JWT_SECRET": "real-secret-value"}'}
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    provider = AWSSecretsManagerProvider(secret_id="my-secret", region_name="us-east-1")
    secrets = provider.get_all_secrets()

    assert secrets == {"DRIVER_JWT_SECRET": "real-secret-value"}
    fake_boto3.client.assert_called_once_with("secretsmanager", region_name="us-east-1")
    fake_client.get_secret_value.assert_called_once_with(SecretId="my-secret")


def test_the_real_boto3_client_reads_the_secret(monkeypatch):
    """The fakes above pass whether or not boto3 is installed, which is how the
    API came to be one ModuleNotFoundError from not starting on ECS. This runs
    the provider against the real client, stubbed at the wire."""
    import boto3
    from botocore.stub import Stubber

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    secret_id = "arn:aws:secretsmanager:us-east-1:000000000000:secret:lmx"
    client = boto3.client("secretsmanager", region_name="us-east-1")
    stubber = Stubber(client)
    stubber.add_response(
        "get_secret_value",
        {"SecretString": '{"DATABASE_URL": "postgresql+asyncpg://db/lmx"}'},
        {"SecretId": secret_id},
    )
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client)

    with stubber:
        secrets = AWSSecretsManagerProvider(secret_id=secret_id, region_name="us-east-1").get_all_secrets()

    assert secrets == {"DATABASE_URL": "postgresql+asyncpg://db/lmx"}
    stubber.assert_no_pending_responses()
