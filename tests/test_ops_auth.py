"""
Ops dashboard per-user auth (roadmap item S1): token round-trip, the
middleware's Bearer-token path with role gating for /admin/*, and the
three-way JWT secret distinctness check.
"""
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.ops_auth.tokens import (
    InvalidOpsToken,
    assert_ops_jwt_secret_configured,
    decode_token,
    issue_token,
)
from app.security import API_KEY_HEADER, SharedSecretAuthMiddleware


async def _ok(request):
    return PlainTextResponse("ok")


def _build_app() -> Starlette:
    routes = [
        Route("/fleet/{hub_id}/drivers", _ok),
        Route("/admin/ops-users", _ok, methods=["GET", "POST"]),
        Route("/ops/auth/login", _ok, methods=["POST"]),
    ]
    test_app = Starlette(routes=routes)
    test_app.add_middleware(SharedSecretAuthMiddleware)
    return test_app


def test_issue_and_decode_roundtrip_carries_role():
    token = issue_token("user-1", "admin")
    assert decode_token(token) == ("user-1", "admin")


def test_decode_rejects_garbage():
    with pytest.raises(InvalidOpsToken):
        decode_token("not-a-token")


def test_refuses_default_secret_outside_development():
    with patch("app.ops_auth.tokens.settings") as mock_settings:
        mock_settings.ops_jwt_secret = "dev-only-insecure-secret-change-in-production"
        mock_settings.environment = "production"
        with pytest.raises(RuntimeError):
            assert_ops_jwt_secret_configured()


def test_three_way_secret_distinctness():
    from app.config import assert_jwt_secrets_are_distinct

    with patch("app.config.settings") as mock_settings:
        mock_settings.environment = "production"
        mock_settings.driver_jwt_secret = "secret-a"
        mock_settings.client_jwt_secret = "secret-b"
        mock_settings.ops_jwt_secret = "secret-a"  # collides with driver
        with pytest.raises(RuntimeError):
            assert_jwt_secrets_are_distinct()

    with patch("app.config.settings") as mock_settings:
        mock_settings.environment = "production"
        mock_settings.driver_jwt_secret = "secret-a"
        mock_settings.client_jwt_secret = "secret-b"
        mock_settings.ops_jwt_secret = "secret-c"
        assert_jwt_secrets_are_distinct()  # must not raise


def test_operator_token_passes_ops_routes_but_not_admin():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        operator_token = issue_token("user-op", "operator")
        client = TestClient(_build_app())

        ok = client.get(
            "/fleet/hub-1/drivers", headers={"Authorization": f"Bearer {operator_token}"}
        )
        assert ok.status_code == 200

        forbidden = client.get(
            "/admin/ops-users", headers={"Authorization": f"Bearer {operator_token}"}
        )
        assert forbidden.status_code == 403


def test_admin_token_passes_admin_routes():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        admin_token = issue_token("user-adm", "admin")
        client = TestClient(_build_app())
        response = client.get(
            "/admin/ops-users", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code == 200


def test_invalid_bearer_is_401_even_in_open_mode():
    # A presented-but-invalid token must be rejected, not silently ignored
    # in favor of open mode - otherwise a stale token looks like success
    # with mysteriously missing permissions later.
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = None
        client = TestClient(_build_app())
        response = client.get(
            "/fleet/hub-1/drivers", headers={"Authorization": "Bearer garbage"}
        )
        assert response.status_code == 401


def test_shared_secret_still_works_as_bootstrap_for_admin_paths():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        client = TestClient(_build_app())
        response = client.post("/admin/ops-users", headers={API_KEY_HEADER: "topsecret"})
        assert response.status_code == 200


def test_ops_login_path_is_reachable_without_any_credential():
    with patch("app.security.settings") as mock_settings:
        mock_settings.api_shared_secret = "topsecret"
        client = TestClient(_build_app())
        response = client.post("/ops/auth/login")
        assert response.status_code == 200
