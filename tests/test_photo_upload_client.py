"""
app/storage/photo_upload_client.py - same "unconfigured -> stub" pattern
as app/messaging/push_client.py. boto3 isn't an installed dependency
(deliberately - see app/secrets_provider.py's precedent), so
S3PhotoUploadClient is tested by injecting a fake module into
sys.modules, same technique as tests/test_secrets_provider.py.
"""
import sys
from unittest.mock import MagicMock, patch

from app.storage.photo_upload_client import (
    S3PhotoUploadClient,
    StubPhotoUploadClient,
    generate_object_key,
    get_photo_upload_client,
)


def test_get_photo_upload_client_defaults_to_stub():
    with patch("app.storage.photo_upload_client.settings") as mock_settings:
        mock_settings.photo_upload_bucket = None
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


def test_s3_client_generates_a_presigned_url_and_a_public_final_url():
    fake_boto3 = MagicMock()
    fake_s3_client = MagicMock()
    fake_s3_client.generate_presigned_url.return_value = "https://s3.amazonaws.com/signed-put-url"
    fake_boto3.client.return_value = fake_s3_client

    with patch.dict(sys.modules, {"boto3": fake_boto3}):
        client = S3PhotoUploadClient(bucket="lmx-pod-photos", region="us-east-1")
        upload = client.create_upload("pod/driver-1/stop-1/photo-abc.jpg", "image/jpeg")

    fake_boto3.client.assert_called_once_with("s3", region_name="us-east-1")
    fake_s3_client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={
            "Bucket": "lmx-pod-photos",
            "Key": "pod/driver-1/stop-1/photo-abc.jpg",
            "ContentType": "image/jpeg",
        },
        ExpiresIn=300,
    )
    assert upload.upload_url == "https://s3.amazonaws.com/signed-put-url"
    assert upload.final_url == "https://lmx-pod-photos.s3.us-east-1.amazonaws.com/pod/driver-1/stop-1/photo-abc.jpg"
    assert upload.requires_upload is True


def test_generate_object_key_is_namespaced_and_unique():
    key_a = generate_object_key("driver-1", "stop-1", "photo", "jpg")
    key_b = generate_object_key("driver-1", "stop-1", "photo", "jpg")
    assert key_a.startswith("pod/driver-1/stop-1/photo-")
    assert key_a.endswith(".jpg")
    assert key_a != key_b  # never collide across two captures for the same stop
