"""
app/storage/photo_upload_client.py - same "unconfigured -> stub" pattern
as app/messaging/push_client.py.

The chooser is tested with a fake boto3 injected into sys.modules. The links
themselves are tested against a real boto3 client with fake credentials:
presigning is local arithmetic with no network call, and a mock would have
happily returned the SigV2 links S3 refuses on any bucket created since 2020.
"""
import sys
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from app.storage.photo_upload_client import (
    READ_URL_EXPIRY_SECONDS,
    UPLOAD_URL_EXPIRY_SECONDS,
    S3PhotoUploadClient,
    StubPhotoUploadClient,
    generate_object_key,
    get_photo_upload_client,
    readable_url,
)

from tests.conftest import PHOTO_BUCKET, PHOTO_REGION

OBJECT_URL = (
    f"https://{PHOTO_BUCKET}.s3.{PHOTO_REGION}.amazonaws.com/pod/driver-1/stop-1/photo-abc.jpg"
)


def _signed(url: str) -> tuple[str, dict[str, str]]:
    """(scheme://host/path, query) for a presigned URL."""
    parts = urlsplit(url)
    query = {key: values[0] for key, values in parse_qs(parts.query).items()}
    return f"{parts.scheme}://{parts.netloc}{parts.path}", query


def test_get_photo_upload_client_defaults_to_stub():
    with patch("app.storage.photo_upload_client.settings") as mock_settings:
        mock_settings.photo_upload_bucket = None
        # Also no local directory. Patching the whole settings object makes
        # every unset attribute a truthy MagicMock, so a new backend added to
        # this chooser silently captures this test unless it opts out - which
        # is exactly what happened when `LocalPhotoUploadClient` landed.
        mock_settings.photo_storage_dir = None
        client = get_photo_upload_client()
    assert isinstance(client, StubPhotoUploadClient)
    assert client.engine_name == "stub"


def test_get_photo_upload_client_uses_s3_when_bucket_configured():
    with patch("app.storage.photo_upload_client.settings") as mock_settings:
        mock_settings.photo_upload_bucket = "lmx-pod-photos"
        mock_settings.photo_upload_region = "us-east-1"
        fake_boto3 = MagicMock()
        with patch.dict(sys.modules, {"boto3": fake_boto3}):
            client = get_photo_upload_client()
    assert isinstance(client, S3PhotoUploadClient)
    assert client.engine_name == "s3"


def test_stub_client_returns_a_local_marker_needing_no_upload():
    upload = StubPhotoUploadClient().create_upload("pod/driver-1/stop-1/photo-abc.jpg", "image/jpeg")
    assert upload.upload_url == "local-capture://pod/driver-1/stop-1/photo-abc.jpg"
    assert upload.final_url == upload.upload_url
    assert upload.requires_upload is False


def test_the_upload_link_is_sigv4_on_the_regional_endpoint(photo_bucket):
    """Left to its defaults, boto3 presigned this with SigV2 against the global
    endpoint, and S3 refuses SigV2 on every bucket created since June 2020 - so
    on the bucket infra/aws creates, no driver photo or document could upload."""
    upload = S3PhotoUploadClient(bucket=PHOTO_BUCKET, region=PHOTO_REGION).create_upload(
        "pod/driver-1/stop-1/photo-abc.jpg", "image/jpeg"
    )

    where, query = _signed(upload.upload_url)
    assert where == OBJECT_URL
    assert query["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"
    assert query["X-Amz-Credential"].endswith(f"/{PHOTO_REGION}/s3/aws4_request")
    assert query["X-Amz-Expires"] == str(UPLOAD_URL_EXPIRY_SECONDS)
    # The content type is signed, so the PUT can't land as some other type.
    assert "content-type" in query["X-Amz-SignedHeaders"].split(";")
    assert upload.final_url == OBJECT_URL
    assert upload.requires_upload is True


def test_a_stored_object_url_reads_back_as_a_signed_link(photo_bucket):
    """The bucket is private, so the object URL stored at upload opens for
    nobody: not the receiver on the tracking page, not the reviewer of a
    licence. What goes out in a response is a signed GET for the same object."""
    where, query = _signed(readable_url(OBJECT_URL))

    assert where == OBJECT_URL
    assert query["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"
    assert query["X-Amz-Expires"] == str(READ_URL_EXPIRY_SECONDS)
    assert query["X-Amz-Signature"]


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "",
        "local-capture://pod/driver-1/stop-1/photo-abc.jpg",
        "http://localhost:8000/public/media/pod/driver-1/stop-1/photo-abc.jpg",
        "https://some-other-bucket.s3.us-east-1.amazonaws.com/pod/driver-1/stop-1/photo-abc.jpg",
    ],
)
def test_what_it_cannot_sign_comes_back_unchanged(photo_bucket, stored):
    assert readable_url(stored) == stored


def test_without_a_bucket_nothing_is_signed(monkeypatch):
    monkeypatch.setattr("app.storage.photo_upload_client.settings.photo_upload_bucket", None)

    assert readable_url(OBJECT_URL) == OBJECT_URL


def test_generate_object_key_is_namespaced_and_unique():
    key_a = generate_object_key("driver-1", "stop-1", "photo", "jpg")
    key_b = generate_object_key("driver-1", "stop-1", "photo", "jpg")
    assert key_a.startswith("pod/driver-1/stop-1/photo-")
    assert key_a.endswith(".jpg")
    assert key_a != key_b  # never collide across two captures for the same stop
