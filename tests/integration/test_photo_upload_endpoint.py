"""
POST /driver/stops/{stop_id}/upload-url (docs/ROADMAP.md A2/A3) against
real Postgres - ownership checks and the stub-vs-real client selection
end to end through the real endpoint, not just the client module in
isolation (see tests/test_photo_upload_client.py for that).
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.driver_routes import create_upload_url
from app.driver_auth.dependencies import AuthedDriver
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.route import Route
from app.models.stop import Stop
from app.schemas.driver_app import UploadUrlRequestBody

pytestmark = pytest.mark.integration


async def _seed_stop(db_session) -> tuple[uuid.UUID, uuid.UUID]:
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Upload Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Driver(id=driver_id, hub_id=hub_id, name="Sam D.", phone="+15555550302", vehicle_capacity_units=5))
    route_id = uuid.uuid4()
    db_session.add(Route(id=route_id, hub_id=hub_id, driver_id=driver_id, status="active"))
    await db_session.commit()
    stop_id = uuid.uuid4()
    db_session.add(
        Stop(
            id=stop_id, route_id=route_id, sequence=1, status="arrived", stop_type="dropoff",
            parcel_count=1, scanned_count=0,
        )
    )
    await db_session.commit()
    return driver_id, stop_id


async def test_returns_a_stub_marker_when_unconfigured(db_session, real_redis_client):
    driver_id, stop_id = await _seed_stop(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id="irrelevant", device_id="device-a")

    with patch("app.storage.photo_upload_client.settings") as mock_settings:
        mock_settings.photo_upload_bucket = None
        result = await create_upload_url(
            str(stop_id), UploadUrlRequestBody(kind="photo", content_type="image/jpeg"),
            driver=authed, session=db_session,
        )

    assert result.requires_upload is False
    assert result.upload_url.startswith("local-capture://pod/")
    assert result.upload_url == result.final_url


async def test_returns_a_real_presigned_url_when_configured(db_session, real_redis_client):
    driver_id, stop_id = await _seed_stop(db_session)
    authed = AuthedDriver(driver_id=str(driver_id), hub_id="irrelevant", device_id="device-a")

    fake_s3_client = MagicMock()
    fake_s3_client.generate_presigned_url.return_value = "https://s3.amazonaws.com/signed"
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_s3_client

    with patch("app.storage.photo_upload_client.settings") as mock_settings:
        mock_settings.photo_upload_bucket = "lmx-pod-photos"
        mock_settings.photo_upload_region = "us-east-1"
        with patch.dict("sys.modules", {"boto3": fake_boto3}):
            result = await create_upload_url(
                str(stop_id), UploadUrlRequestBody(kind="signature", content_type="image/png"),
                driver=authed, session=db_session,
            )

    assert result.requires_upload is True
    assert result.upload_url == "https://s3.amazonaws.com/signed"
    assert result.final_url.startswith("https://lmx-pod-photos.s3.us-east-1.amazonaws.com/pod/")


async def test_404s_for_a_stop_belonging_to_another_driver(db_session, real_redis_client):
    _driver_id, stop_id = await _seed_stop(db_session)
    other_hub_id, other_driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=other_hub_id, name="Other Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(
        Driver(id=other_driver_id, hub_id=other_hub_id, name="Other Driver", phone="+15555550303", vehicle_capacity_units=5)
    )
    await db_session.commit()
    authed_other = AuthedDriver(driver_id=str(other_driver_id), hub_id="irrelevant", device_id="device-b")

    with pytest.raises(HTTPException) as exc_info:
        await create_upload_url(
            str(stop_id), UploadUrlRequestBody(kind="photo", content_type="image/jpeg"),
            driver=authed_other, session=db_session,
        )
    assert exc_info.value.status_code == 404
