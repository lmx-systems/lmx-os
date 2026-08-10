"""
Presigned upload for driver compliance documents (docs/ROADMAP.md R4).

Mechanically identical to `app/storage/photo_upload_client.py` and deliberately
built on it rather than beside it - one presigned-PUT implementation, one
"unconfigured -> stub" fallback, one place where a bucket misconfiguration shows
up.

**What is NOT shared is the key space, and that matters more than it looks.** A
proof-of-delivery photo is a picture of a doorstep; a driver's license scan is
government photo ID. They have different retention obligations, different access
rules, and different answers to "who may look at this" - so they must at least be
separable by prefix, and ideally live in their own bucket with its own lifecycle
policy once R3's retention policy exists. `DOCUMENT_KEY_PREFIX` is what makes that
split possible later without a data migration; a note rather than a solution,
because the policy it would implement hasn't been written yet.

**The point of routing uploads through here at all** is that `file_url` stops
being a client-supplied string. Before this, a driver could PUT
`{"file_url": "https://example.com/anything"}` and the system stored it as their
license scan. Now the backend mints the key, hands back a URL scoped to that key,
and writes `file_url` itself - so the row points at something we actually hold.
"""
from __future__ import annotations

import uuid

import structlog

from app.storage.photo_upload_client import (
    PhotoUploadClient,
    PresignedUpload,
    S3PhotoUploadClient,
    StubPhotoUploadClient,
)
from app.config import settings

logger = structlog.get_logger(__name__)

# Kept separate from the `pod/` prefix so compliance evidence can be given its own
# bucket, access policy and retention rule without moving objects around.
DOCUMENT_KEY_PREFIX = "driver-documents"

# What a phone camera or a scanning app actually produces. Restricted because
# content_type ends up in the presigned policy: allowing anything would let a
# driver upload an HTML file that renders as a page when a reviewer opens the
# "scan", which is a stored-XSS shape rather than a compliance document.
ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/heic", "application/pdf")

_EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "application/pdf": "pdf",
}


class UnsupportedDocumentType(Exception):
    pass


def document_object_key(driver_id: str, doc_type: str, content_type: str) -> str:
    """Where this document will live.

    A fresh uuid per upload rather than a stable `driver/doc_type` path: a driver
    re-uploading after a rejection must not overwrite the evidence a reviewer
    already looked at, or the audit trail rewrites itself.
    """
    extension = _EXTENSION_BY_CONTENT_TYPE.get(content_type)
    if extension is None:
        raise UnsupportedDocumentType(
            f"{content_type} is not an accepted document format"
        )
    return f"{DOCUMENT_KEY_PREFIX}/{driver_id}/{doc_type}/{uuid.uuid4().hex}.{extension}"


def get_document_upload_client() -> PhotoUploadClient:
    """The same S3-or-stub selection as POD photos, on the same bucket setting.

    Sharing the bucket setting is a pilot-scale decision, not a statement that
    these belong together - see the module docstring. When R3 lands, this is the
    one function that has to change.
    """
    if settings.photo_upload_bucket:
        return S3PhotoUploadClient(
            bucket=settings.photo_upload_bucket, region=settings.photo_upload_region
        )
    logger.warning(
        "document_upload_client_selected",
        engine="stub",
        reason="PHOTO_UPLOAD_BUCKET is not set - compliance documents are not really stored",
    )
    return StubPhotoUploadClient()


def create_document_upload(
    driver_id: str, doc_type: str, content_type: str
) -> tuple[PresignedUpload, str]:
    """(presigned upload, the key it writes to)."""
    key = document_object_key(driver_id, doc_type, content_type)
    return get_document_upload_client().create_upload(key, content_type), key
