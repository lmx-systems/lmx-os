"""Storing and serving proof-of-delivery photos on local disk.

A demo backend, and still the code path that takes bytes off a handset and puts
them on a filesystem by a name that arrived in a URL. Path traversal is the
obvious attack and `startswith` is the obvious wrong answer, so both are tested
here rather than reasoned about in a docstring.

The GET being unauthenticated is deliberate and is asserted, not tolerated: an
`<img src>` cannot send an Authorization header, and S3's `final_url` is
unauthenticated for the same reason. A test that pinned it shut would be
pinning down a different design.
"""
from __future__ import annotations

import re

import pytest
from fastapi import HTTPException

from app.api import media_routes
from app.config import settings
from app.storage.photo_upload_client import (
    LocalPhotoUploadClient,
    StubPhotoUploadClient,
    generate_object_key,
    get_photo_upload_client,
)

DRIVER = "11111111-1111-1111-1111-111111111111"
STOP = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "photo_storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "environment", "development")
    return tmp_path


class TestTheKeyIsNotTrusted:
    """It arrives in the URL path, so it is an input, not a name."""

    @pytest.mark.parametrize(
        "key",
        [
            "../../etc/passwd",
            "pod/../../../etc/passwd",
            f"pod/{DRIVER}/{STOP}/../../../../etc/passwd",
            "pod/x/y/photo-abc.jpg",          # ids are not uuids
            f"pod/{DRIVER}/{STOP}/photo-abc.jpg",   # suffix is not a uuid4 hex
            f"pod/{DRIVER}/{STOP}/photo-{'a' * 32}.exe",  # not an image
            f"pod/{DRIVER}/{STOP}/script-{'a' * 32}.jpg",  # not a kind we issue
            "",
        ],
    )
    def test_anything_this_server_did_not_issue_is_refused(self, storage, key):
        with pytest.raises(HTTPException) as exc:
            media_routes._resolved(key)
        assert exc.value.status_code in (400, 404)

    def test_a_key_this_server_did_issue_resolves(self, storage):
        key = generate_object_key(DRIVER, STOP, "photo", "jpg")

        resolved = media_routes._resolved(key)

        assert resolved.parent.parent.parent == storage / "pod"

    def test_a_sibling_directory_with_a_shared_prefix_is_not_inside(self, tmp_path, monkeypatch):
        # The reason the check is `relative_to` and not `startswith`:
        # `/srv/pod-evil` starts with `/srv/pod` and is a different directory.
        root = tmp_path / "pod"
        root.mkdir()
        (tmp_path / "pod-evil").mkdir()
        monkeypatch.setattr(settings, "photo_storage_dir", str(root))
        monkeypatch.setattr(settings, "environment", "development")

        with pytest.raises(HTTPException):
            media_routes._resolved("../pod-evil/secret.jpg")

    def test_it_refuses_when_no_directory_is_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "photo_storage_dir", None)

        with pytest.raises(HTTPException) as exc:
            media_routes._resolved(generate_object_key(DRIVER, STOP, "photo", "jpg"))
        assert exc.value.status_code == 404


class TestTheKeysThisServerIssues:
    def test_every_generated_key_matches_what_the_route_accepts(self):
        # The two halves have to agree or every upload 400s. They are in
        # different modules, which is exactly how they would drift.
        for kind in ("photo", "signature", "barcode"):
            for extension in ("jpg", "png", "webp"):
                key = generate_object_key(DRIVER, STOP, kind, extension)
                assert media_routes._KEY.match(key), key

    def test_the_filename_carries_a_uuid(self):
        # The unguessable part. It is what stands in for access control on the
        # GET, so a key that became predictable would quietly remove the only
        # thing protecting a customer's delivery photo.
        key = generate_object_key(DRIVER, STOP, "photo", "jpg")
        assert re.search(r"photo-[0-9a-f]{32}\.jpg$", key)

    def test_two_captures_of_the_same_kind_never_collide(self):
        first = generate_object_key(DRIVER, STOP, "photo", "jpg")
        second = generate_object_key(DRIVER, STOP, "photo", "jpg")
        assert first != second


class TestChoosingTheBackend:
    def test_a_bucket_wins_over_local_disk(self, monkeypatch):
        # A demo backend must never take precedence over durable storage, and a
        # stack with both configured is exactly how that would happen by
        # accident. S3PhotoUploadClient is stubbed because constructing the real
        # one builds a boto3 client; what is under test is the choice.
        class FakeS3:
            engine_name = "s3"

            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("app.storage.photo_upload_client.S3PhotoUploadClient", FakeS3)
        monkeypatch.setattr(settings, "photo_upload_bucket", "a-real-bucket")
        monkeypatch.setattr(settings, "photo_storage_dir", "/tmp/whatever")
        monkeypatch.setattr(settings, "environment", "development")

        assert get_photo_upload_client().engine_name == "s3"

    def test_local_disk_is_used_when_there_is_no_bucket(self, monkeypatch):
        monkeypatch.setattr(settings, "photo_upload_bucket", None)
        monkeypatch.setattr(settings, "photo_storage_dir", "/tmp/whatever")
        monkeypatch.setattr(settings, "environment", "development")

        assert get_photo_upload_client().engine_name == "local"

    def test_neither_configured_still_falls_back_to_the_stub(self, monkeypatch):
        monkeypatch.setattr(settings, "photo_upload_bucket", None)
        monkeypatch.setattr(settings, "photo_storage_dir", None)

        assert isinstance(get_photo_upload_client(), StubPhotoUploadClient)

    def test_local_disk_refuses_to_run_outside_development(self, monkeypatch):
        # Local disk loses every photo on redeploy and a POD photo is evidence
        # in a dispute. Trusting nobody will point this at production is not a
        # control.
        monkeypatch.setattr(settings, "environment", "production")

        with pytest.raises(RuntimeError, match="development-only"):
            LocalPhotoUploadClient(directory="/tmp/pod", base_url="http://x")

    def test_the_upload_and_final_urls_are_the_same_place(self, monkeypatch):
        monkeypatch.setattr(settings, "environment", "development")
        client = LocalPhotoUploadClient(directory="/tmp/pod", base_url="http://10.0.0.5:8000/")
        key = generate_object_key(DRIVER, STOP, "photo", "jpg")

        upload = client.create_upload(key, "image/jpeg")

        # The base URL is used verbatim, trailing slash normalised. It matters
        # that this is configurable at all: on a handset `localhost` means the
        # handset, so a demo pointed at the default fails in the one place it
        # is meant to work.
        assert upload.upload_url == upload.final_url == f"http://10.0.0.5:8000/media/{key}"
        assert upload.requires_upload is True


class TestARoundTrip:
    """Bytes in, the same bytes out, by the URL the app was handed."""

    async def _put(self, key, body, storage):
        class _Request:
            async def body(self):
                return body

        return await media_routes.upload_media(
            key=key,
            request=_Request(),
            driver=_FakeDriver(),
        )

    async def test_a_capture_comes_back_byte_for_byte(self, storage):
        key = generate_object_key(DRIVER, STOP, "photo", "jpg")
        png = b"\x89PNG\r\n\x1a\n" + b"pretend this is a doorstep" * 8

        await self._put(key, png, storage)
        response = await media_routes.fetch_media(key=key)

        assert response.path.read_bytes() == png
        assert response.media_type == "image/jpeg"

    async def test_a_capture_that_was_never_uploaded_is_a_404(self, storage):
        key = generate_object_key(DRIVER, STOP, "photo", "jpg")

        with pytest.raises(HTTPException) as exc:
            await media_routes.fetch_media(key=key)
        assert exc.value.status_code == 404

    async def test_an_empty_upload_is_refused(self, storage):
        # A zero-byte photo is a failed capture, and storing it would put a
        # broken image on a tracking page as proof of delivery.
        key = generate_object_key(DRIVER, STOP, "photo", "jpg")

        with pytest.raises(HTTPException) as exc:
            await self._put(key, b"", storage)
        assert exc.value.status_code == 400

    async def test_an_oversized_capture_is_refused(self, storage):
        key = generate_object_key(DRIVER, STOP, "photo", "jpg")

        with pytest.raises(HTTPException) as exc:
            await self._put(key, b"x" * (media_routes.MAX_BYTES + 1), storage)
        assert exc.value.status_code == 413

    async def test_a_capture_is_cached_forever_because_the_key_is_unique(self, storage):
        # A new photo is a new URL, so the bytes behind one never change. A
        # tracking page polls; without this it refetches the same image every
        # time.
        key = generate_object_key(DRIVER, STOP, "photo", "jpg")
        await self._put(key, b"jpeg-ish", storage)

        response = await media_routes.fetch_media(key=key)

        assert "immutable" in response.headers["cache-control"]


class _FakeDriver:
    driver_id = DRIVER
    device_id = "device-1"
