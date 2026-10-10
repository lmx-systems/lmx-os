"""Google credentials for Route Optimization, including from AWS with no key file.

Route Optimization is a Cloud IAM API, so it needs an OAuth token with the
`cloud-platform` scope, not the Maps API key (docs/E1_ROUTE_OPTIMIZATION_ACCESS.md).

Two ways to get one:

  - **Application Default Credentials.** gcloud user credentials on a laptop, or
    workload identity on Google Cloud. What every run so far has used.
  - **Workload identity federation from AWS.** LMX runs on ECS, where there is no
    Google identity to inherit, and the repo's rule is not to download a
    long-lived service-account key. Google trusts the ECS task's own AWS role
    instead: the task signs a request with its temporary AWS credentials, Google's
    token service checks it against a workload identity pool, and hands back a
    short-lived Google token. Set `GOOGLE_WIF_AUDIENCE` (the pool provider) and,
    if the pool impersonates a service account, `GOOGLE_WIF_SERVICE_ACCOUNT`.
    Nothing here is a secret.

The AWS credentials come from boto3's own chain, which on Fargate is the task
role, so they rotate the way AWS rotates them.
"""
from __future__ import annotations

import threading

import boto3
import google.auth
import google.auth.aws
import google.auth.credentials
import google.auth.exceptions

from app.config import settings

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_AWS_SUBJECT_TOKEN_TYPE = "urn:ietf:params:aws:token-type:aws4_request"


class _Boto3AwsSupplier(google.auth.aws.AwsSecurityCredentialsSupplier):
    """The ECS task's AWS credentials, through boto3's credential chain.

    boto3 refreshes the task role's temporary credentials itself; asking for the
    frozen set on each call is cheap and always current, which covers the caching
    google-auth leaves to the supplier.
    """

    def __init__(self) -> None:
        self._session = boto3.Session()
        self._lock = threading.Lock()

    def get_aws_security_credentials(self, context, request):
        with self._lock:
            credentials = self._session.get_credentials()
            if credentials is None:
                raise google.auth.exceptions.RefreshError(
                    "No AWS credentials found for Google workload identity federation"
                )
            frozen = credentials.get_frozen_credentials()
        return google.auth.aws.AwsSecurityCredentials(
            frozen.access_key, frozen.secret_key, frozen.token
        )

    def get_aws_region(self, context, request):
        region = self._session.region_name or settings.photo_upload_region
        if not region:
            raise google.auth.exceptions.RefreshError(
                "No AWS region found for Google workload identity federation"
            )
        return region


def route_optimization_credentials() -> google.auth.credentials.Credentials:
    """Credentials for Route Optimization, or an exception saying why there are none.

    Called on first use, not at startup: a misconfiguration has to surface as a
    failed solve the fallback planner absorbs (app/optimizer/fallback.py), never
    as a dispatch cycle that can't even build its client.
    """
    if settings.google_wif_audience:
        impersonation_url = (
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            f"{settings.google_wif_service_account}:generateAccessToken"
            if settings.google_wif_service_account
            else None
        )
        return google.auth.aws.Credentials(
            audience=settings.google_wif_audience,
            subject_token_type=_AWS_SUBJECT_TOKEN_TYPE,
            aws_security_credentials_supplier=_Boto3AwsSupplier(),
            service_account_impersonation_url=impersonation_url,
            scopes=[CLOUD_PLATFORM_SCOPE],
        )
    credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    return credentials
