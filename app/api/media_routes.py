"""Storing and serving proof-of-delivery photos on local disk. **Dev only.**

The counterpart to `LocalPhotoUploadClient`. With `PHOTO_UPLOAD_BUCKET` set,
the driver app PUTs straight to S3 and this module is never mounted; without a
bucket *and* without `PHOTO_STORAGE_DIR`, the stub issues a `local-capture://`
marker and nothing is stored at all.

It exists because "delivered, with proof" is not demonstrable when the proof is
a placeholder URL. A real photo, taken on a real handset, has to arrive
somewhere a console and a tracking page can render it.

## What is guarded, and what a reader should not assume is guarded

**The PUT is authenticated as the driver**, which is stronger than the S3 path
rather than weaker: a presigned URL cannot check who is holding it.

**The GET is not authenticated**, and that is deliberate and load-bearing. An
`<img src>` in the ops console and on a recipient's tracking page cannot send an
Authorization header, and S3's `final_url` is unauthenticated for exactly the
same reason. The `uuid4` that `generate_object_key` puts in every filename is
what stands in for access control. **That is a capability URL, not a permission
check** - anyone holding the link can see the photo, on this backend and on S3
alike. Whoever swaps in a signed-URL scheme should change both.

**The key is validated, not trusted.** It arrives in the URL path, so
`..%2f..%2fetc%2fpasswd` is the obvious attack and a `startswith` check on the
resolved path is the obvious wrong answer - `/srv/pod-evil` starts with
`/srv/pod`. The key is matched against the exact shape `generate_object_key`
emits and the resolved path is checked with `relative_to`, which compares path
components rather than characters.
"""
from __future__ import annotations

import re
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.config import settings
from app.driver_auth.dependencies import AuthedDriver, get_current_driver

logger = structlog.get_logger(__name__)

# **Two routers, two prefixes, and the split is the security design.**
#
# `OpsUserAuthMiddleware` guards everything that is not explicitly exempt, and
# its exemption list carries a warning in capitals: *add nothing here that does
# not authenticate itself*. A single `/media` prefix would have needed a new
# exemption covering a GET that genuinely does not - so instead each half lands
# under the prefix whose documented rationale already covers it.
#
# `/driver` is exempt because "each has its own real per-account auth already",
# which is exactly true of the upload: it depends on `get_current_driver`.
#
# `/public` is exempt because it is "genuinely unauthenticated ... safe for
# anyone on the internet to call", which is exactly what a capability URL is.
#
# The upload and fetch URLs therefore differ, as they do on S3, where a
# presigned PUT and a plain object URL are also not the same address.
driver_router = APIRouter(prefix="/driver/media", tags=["media"])
public_router = APIRouter(prefix="/public/media", tags=["media"])

# Exactly what `app/storage/photo_upload_client.py`'s `generate_object_key`
# emits: `pod/<driver uuid>/<stop uuid>/<kind>-<uuid4 hex>.<ext>`. Anchored, and
# no `.` allowed in a path segment, so no traversal sequence can be spelled at
# all - the `relative_to` check below is the second of two locks rather than the
# only one.
_KEY = re.compile(
    r"^pod/[0-9a-fA-F-]{36}/[0-9a-fA-F-]{36}/"
    r"(photo|signature|barcode)-[0-9a-f]{32}\.(jpg|png|webp)$"
)

_MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

# A proof-of-delivery photo off a handset. Generous enough for a full-resolution
# capture, small enough that a bad actor with a driver token cannot fill the
# disk in one request.
MAX_BYTES = 12 * 1024 * 1024


def _resolved(key: str) -> Path:
    """The file this key names, or a 400/404 rather than a guess."""
    if settings.photo_storage_dir is None:
        raise HTTPException(status_code=404, detail="Local media storage is not configured")
    if not _KEY.match(key):
        raise HTTPException(status_code=400, detail="Not a media key this server issues")

    root = Path(settings.photo_storage_dir).resolve()
    candidate = (root / key).resolve()
    try:
        # Component-wise, not `startswith`: `/srv/pod-evil` starts with
        # `/srv/pod` and is a different directory.
        candidate.relative_to(root)
    except ValueError:  # pragma: no cover - unreachable while _KEY holds
        raise HTTPException(status_code=400, detail="Not a media key this server issues") from None
    return candidate


@driver_router.put("/{key:path}", status_code=204)
async def upload_media(
    key: str,
    request: Request,
    driver: AuthedDriver = Depends(get_current_driver),
) -> Response:
    """Store one captured file. The driver app's direct-upload target.

    Authenticated as the driver, unlike a presigned S3 URL. The key is not
    checked against *this* driver's id: a key is minted by
    `POST /driver/stops/{id}/upload-url`, which already refuses a stop the
    driver does not own, and re-deriving ownership from a path component would
    be trusting the path to say who the driver is.
    """
    path = _resolved(key)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(body) > MAX_BYTES:
        raise HTTPException(
            status_code=413, detail=f"Capture is larger than {MAX_BYTES // (1024 * 1024)}MB"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    logger.info("media_stored", key=key, bytes=len(body), driver_id=driver.driver_id)
    return Response(status_code=204)


@public_router.get("/{key:path}")
async def fetch_media(key: str) -> FileResponse:
    """Serve one stored file.

    Unauthenticated by design - see the module docstring. The unguessable key is
    the capability, exactly as it is on the S3 path.
    """
    path = _resolved(key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such capture")
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES[key.rsplit(".", 1)[1]],
        # A capture never changes once written - the key carries a uuid4, so a
        # new photo is a new URL. Long-lived caching is safe and keeps a
        # tracking page from re-fetching the same image on every poll.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
