"""
SharedSecretAuthMiddleware, exercised against a bare Starlette app rather
than app.main.app - the real app's lifespan touches live Postgres/Redis,
which isn't available in this offline test suite (see conftest.py).
"""
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.security import API_KEY_HEADER, SharedSecretAuthMiddleware


async def _ok(request):
    return PlainTextResponse("ok")


def _build_app() -> Starlette:
    routes = [
        Route("/health", _ok),
        Route("/fleet/{hub_id}/drivers", _ok),
        Route("/driver/me", _ok),
        Route("/drivers-report", _ok),
    ]
    test_app = Starlette(routes=routes)
    test_app.add_middleware(SharedSecretAuthMiddleware)
    return test_app


def test_secret_unset_leaves_every_path_open():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = None
        client = TestClient(_build_app())
        response = client.get("/fleet/hub-1/drivers")
    assert response.status_code == 200


def test_health_is_exempt_even_with_secret_configured():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        client = TestClient(_build_app())
        response = client.get("/health")
    assert response.status_code == 200


def test_protected_path_rejects_missing_key():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        client = TestClient(_build_app())
        response = client.get("/fleet/hub-1/drivers")
    assert response.status_code == 401


def test_protected_path_rejects_wrong_key():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        client = TestClient(_build_app())
        response = client.get("/fleet/hub-1/drivers", headers={API_KEY_HEADER: "wrong"})
    assert response.status_code == 401


def test_protected_path_accepts_correct_key():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        client = TestClient(_build_app())
        response = client.get("/fleet/hub-1/drivers", headers={API_KEY_HEADER: "topsecret"})
    assert response.status_code == 200


def test_driver_prefix_is_exempt_even_with_secret_configured():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        client = TestClient(_build_app())
        response = client.get("/driver/me")
    assert response.status_code == 200


def test_path_merely_starting_with_the_exempt_word_is_not_exempt():
    # /drivers-report must NOT inherit the /driver exemption just because it
    # starts with the same characters - see app/security.py's _is_exempt.
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        client = TestClient(_build_app())
        response = client.get("/drivers-report")
    assert response.status_code == 401
