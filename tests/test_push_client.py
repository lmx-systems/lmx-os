"""
app/messaging/push_client.py - same "unconfigured -> stub" pattern as
app/messaging/sms_client.py, tested the same way (mock the settings
object, mock the outbound HTTP call).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.messaging.push_client import ExpoPushClient, StubPushClient, get_push_client


def test_get_push_client_defaults_to_stub():
    with patch("app.messaging.push_client.settings") as mock_settings:
        mock_settings.expo_push_enabled = False
        client = get_push_client()
    assert isinstance(client, StubPushClient)
    assert client.engine_name == "stub"


def test_get_push_client_uses_expo_when_enabled():
    with patch("app.messaging.push_client.settings") as mock_settings:
        mock_settings.expo_push_enabled = True
        mock_settings.expo_push_access_token = None
        client = get_push_client()
    assert isinstance(client, ExpoPushClient)
    assert client.engine_name == "expo"


@pytest.mark.asyncio
async def test_stub_push_client_send_does_not_raise():
    await StubPushClient().send("ExponentPushToken[abc]", "Title", "Body")


@pytest.mark.asyncio
async def test_expo_push_client_posts_the_expected_payload():
    client = ExpoPushClient()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"data": {"status": "ok"}}
    client._http.post = AsyncMock(return_value=fake_response)

    await client.send("ExponentPushToken[abc]", "New delivery offer", "2 stops nearby", data={"type": "job_offer"})

    client._http.post.assert_awaited_once()
    _, kwargs = client._http.post.call_args
    assert kwargs["json"] == {
        "to": "ExponentPushToken[abc]",
        "title": "New delivery offer",
        "body": "2 stops nearby",
        "data": {"type": "job_offer"},
    }


@pytest.mark.asyncio
async def test_expo_push_client_logs_a_per_message_error_without_raising():
    client = ExpoPushClient()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"data": {"status": "error", "message": "DeviceNotRegistered"}}
    client._http.post = AsyncMock(return_value=fake_response)

    await client.send("ExponentPushToken[stale]", "Title", "Body")  # must not raise
