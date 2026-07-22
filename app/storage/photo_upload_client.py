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


class S3PhotoUploadClient(PhotoUploadClient):
    engine_name = "s3"

    def __init__(self, bucket: str, region: str) -> None:
        self._bucket = bucket
        self._region = region
        import boto3

        self._client = boto3.client("s3", region_name=region)

    def create_upload(self, key: str, content_type: str) -> PresignedUpload:
        upload_url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=UPLOAD_URL_EXPIRY_SECONDS,
        )
        final_url = f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"
        return PresignedUpload(upload_url=upload_url, final_url=final_url, requires_upload=True)


class StubPhotoUploadClient(PhotoUploadClient):
    engine_name = "stub"

    def create_upload(self, key: str, content_type: str) -> PresignedUpload:
        # Same marker shape this app used before any upload pipeline
        # existed. requires_upload=False - there's no real endpoint to PUT
        # to, the driver app should just use final_url as-is.
        marker = f"local-capture://{key}"
        return PresignedUpload(upload_url=marker, final_url=marker, requires_upload=False)


def get_photo_upload_client() -> PhotoUploadClient:
    if settings.photo_upload_bucket:
        logger.info("photo_upload_client_selected", engine="s3")
        return S3PhotoUploadClient(bucket=settings.photo_upload_bucket, region=settings.photo_upload_region)
    logger.warning(
        "photo_upload_client_selected",
        engine="stub",
        reason="PHOTO_UPLOAD_BUCKET is not set - running in stub mode",
    )
    return StubPhotoUploadClient()


def generate_object_key(driver_id: str, stop_id: str, kind: str, extension: str) -> str:
    return f"pod/{driver_id}/{stop_id}/{kind}-{uuid.uuid4().hex}.{extension}"
