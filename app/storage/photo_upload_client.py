"""
Presigned upload URLs for proof-of-delivery photos/signatures and parcel-
scan barcode images (docs/ROADMAP.md A2/A3). The driver app requests one
of these just before capturing, uploads the file directly to S3 (never
proxied through this backend), then submits the returned `final_url` as
CompleteStopBody.photo_url/signature_url - no schema change needed there,
since those fields already accept a plain string URL.

Same "unconfigured -> stub" shape as app/messaging/sms_client.py:
S3PhotoUploadClient is real, used once PHOTO_UPLOAD_BUCKET is configured;
until then StubPhotoUploadClient issues the same local-capture:// marker
this app used before this pipeline existed (`requires_upload=False` tells
the driver app there's nothing to actually PUT - see
driver-app/src/api/uploadCapturedFile.ts), so nothing downstream needs to
change behavior to keep working without a real bucket.

**`final_url` is where the object lives, not a link anyone can open.** The
bucket is private (infra/aws/s3.tf), so its plain object URL answers 403 to
everyone. It's stored as the record of what was captured, and
`readable_url` turns it into a signed GET each time an API response hands it
to a browser: the tracking page's photo and signature, and a driver
document for its reviewer and its owner.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

# Plenty for a driver to capture one photo/signature and upload it -
# not a long-lived credential, just enough headroom past a slow connection.
UPLOAD_URL_EXPIRY_SECONDS = 300

# How long a link to something stored keeps working. Links are signed per
# response and never stored, so a page that reloads gets a fresh one; an hour
# covers a reviewer who loads the document queue and opens a file later. The
# task role's credentials rotate, and a link can't outlive the credentials
# that signed it, so in practice it can end sooner, never later.
READ_URL_EXPIRY_SECONDS = 60 * 60


@dataclass(frozen=True)
class PresignedUpload:
    upload_url: str
    final_url: str
    requires_upload: bool


class PhotoUploadClient(ABC):
    engine_name: str

    @abstractmethod
    def create_upload(self, key: str, content_type: str) -> PresignedUpload:
        raise NotImplementedError

    def readable_url(self, stored_url: str) -> str:
        """A link a browser can open for a `final_url` this client issued.

        Most backends' `final_url` already is one: the stub's marker opens
        nothing on purpose, and the local backend's is a capability URL it
        serves itself. Only S3's needs signing.
        """
        return stored_url


class S3PhotoUploadClient(PhotoUploadClient):
    engine_name = "s3"

    def __init__(self, bucket: str, region: str) -> None:
        self._bucket = bucket
        self._region = region
        import boto3
        from botocore.config import Config

        # SigV4 on the regional endpoint, said explicitly. Left to its defaults,
        # boto3 presigns with SigV2 against the global endpoint, and S3 refuses
        # SigV2 on every bucket created since June 2020 - which includes every
        # bucket infra/aws creates - so both the upload and the read links
        # would be refused.
        self._client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=f"https://s3.{region}.amazonaws.com",
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
        )

    def _object_url(self, key: str) -> str:
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"

    def create_upload(self, key: str, content_type: str) -> PresignedUpload:
        upload_url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=UPLOAD_URL_EXPIRY_SECONDS,
        )
        return PresignedUpload(
            upload_url=upload_url, final_url=self._object_url(key), requires_upload=True
        )

    def readable_url(self, stored_url: str) -> str:
        prefix = self._object_url("")
        if not stored_url.startswith(prefix):
            # Not an object in this bucket, so nothing this client can sign.
            return stored_url
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": stored_url[len(prefix):]},
            ExpiresIn=READ_URL_EXPIRY_SECONDS,
        )


class StubPhotoUploadClient(PhotoUploadClient):
    engine_name = "stub"

    def create_upload(self, key: str, content_type: str) -> PresignedUpload:
        # Same marker shape this app used before any upload pipeline
        # existed. requires_upload=False - there's no real endpoint to PUT
        # to, the driver app should just use final_url as-is.
        marker = f"local-capture://{key}"
        return PresignedUpload(upload_url=marker, final_url=marker, requires_upload=False)


class LocalPhotoUploadClient(PhotoUploadClient):
    """Photos on local disk, served back by this API. **Development only.**

    Exists so a demo can show a *real* proof-of-delivery photo. Without a
    bucket the stub issues a `local-capture://` marker and nothing is stored -
    which is fine for a test and useless in front of somebody, because "through
    to POD" then means a record that a photo existed rather than a photo anybody
    can look at.

    **Refused outside development, in code.** Local disk loses every photo on
    redeploy, has no lifecycle policy and no CDN, and a POD photo is evidence in
    a dispute: the failure mode is discovering months later that the proof is
    gone. Trusting nobody will point this at production is not a control.

    **The URL is the capability.** `generate_object_key` puts a `uuid4` in every
    filename, and the GET that serves these is unauthenticated: an `<img src>`
    in the console and on a recipient's tracking page cannot send an
    Authorization header. An unguessable path is what stands in for one. That
    is weaker than the S3 path, whose links are signed per response and expire
    within the hour, and acceptable only because this backend never leaves
    development.
    """

    engine_name = "local"

    def __init__(self, directory: str, base_url: str) -> None:
        if settings.environment != "development":
            raise RuntimeError(
                "PHOTO_STORAGE_DIR is a development-only backend and "
                f"ENVIRONMENT is {settings.environment!r}. Local disk loses POD "
                "photos on redeploy and a POD photo is evidence in a dispute - "
                "configure PHOTO_UPLOAD_BUCKET instead."
            )
        self._directory = directory
        self._base_url = base_url.rstrip("/")

    def create_upload(self, key: str, content_type: str) -> PresignedUpload:
        # Different addresses, as on S3, where a presigned PUT and a plain
        # object URL also differ. The upload authenticates as the driver and so
        # lives under `/driver`; the fetch is a capability URL and so lives
        # under `/public` - see `app/api/media_routes.py` for why that split is
        # the security design rather than a routing detail.
        return PresignedUpload(
            upload_url=f"{self._base_url}/driver/media/{key}",
            final_url=f"{self._base_url}/public/media/{key}",
            requires_upload=True,
        )


def get_photo_upload_client() -> PhotoUploadClient:
    if settings.photo_upload_bucket:
        logger.info("photo_upload_client_selected", engine="s3")
        return S3PhotoUploadClient(bucket=settings.photo_upload_bucket, region=settings.photo_upload_region)
    if settings.photo_storage_dir:
        # Checked after the bucket, so a stack with both configured uses the
        # real one. A demo backend must never win over durable storage.
        logger.info("photo_upload_client_selected", engine="local")
        return LocalPhotoUploadClient(
            directory=settings.photo_storage_dir, base_url=settings.media_base_url
        )
    logger.warning(
        "photo_upload_client_selected",
        engine="stub",
        reason="PHOTO_UPLOAD_BUCKET is not set - running in stub mode",
    )
    return StubPhotoUploadClient()


def readable_url(stored_url: str | None) -> str | None:
    """A link a browser can open, for a URL stored from an upload's `final_url`.

    Call it at the edge, on the way out of an API response, and never store
    what it returns: an S3 link is signed and expires. Anything that isn't an
    object in the configured bucket comes back unchanged.
    """
    if not stored_url or not settings.photo_upload_bucket:
        return stored_url
    client = S3PhotoUploadClient(
        bucket=settings.photo_upload_bucket, region=settings.photo_upload_region
    )
    return client.readable_url(stored_url)


def generate_object_key(driver_id: str, stop_id: str, kind: str, extension: str) -> str:
    return f"pod/{driver_id}/{stop_id}/{kind}-{uuid.uuid4().hex}.{extension}"
