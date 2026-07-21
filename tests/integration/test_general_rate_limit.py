"""
GeneralRateLimitMiddleware (docs/ROADMAP.md S5) - needs a real Redis
connection (app.redis_client.get_client() is a module-level singleton
pool, not swappable per-test), so this is an integration test, same
reasoning tests/integration/test_fleet_state_integration.py gives for
testing against real Redis instead of fakeredis.

Calls .dispatch() directly rather than driving it through Starlette's
TestClient - TestClient runs requests on its own internal event loop,
which conflicts with app.redis_client's module-level connection pool
being bound to pytest-asyncio's per-test loop (the same class of "attached
to a different loop" issue tests/integration/conftest.py's
real_redis_client fixture works around for direct app-code calls). Same
"call the function/handler directly" pattern used throughout
tests/integration/.
"""
from unittest.mock import patch

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.rate_limit import GeneralRateLimitMiddleware, _key

pytestmark = pytest.mark.integration


def _fake_request(path: str) -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "client": ("1.2.3.4", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


async def _call_next(request):
    return PlainTextResponse("ok")


async def test_health_is_exempt_from_rate_limiting(real_redis_client):
    with patch("app.rate_limit.settings") as mock_settings:
        mock_settings.general_rate_limit_max_requests = 1
        mock_settings.general_rate_limit_window_seconds = 60
        middleware = GeneralRateLimitMiddleware(app=None)
        for _ in range(5):
            response = await middleware.dispatch(_fake_request("/health"), _call_next)
            assert response.status_code == 200


async def test_requests_under_the_cap_succeed(real_redis_client):
    with patch("app.rate_limit.settings") as mock_settings:
        mock_settings.general_rate_limit_max_requests = 5
        mock_settings.general_rate_limit_window_seconds = 60
        middleware = GeneralRateLimitMiddleware(app=None)
        for _ in range(5):
            response = await middleware.dispatch(_fake_request("/fleet/hub-1/drivers"), _call_next)
            assert response.status_code == 200


async def test_requests_over_the_cap_are_rejected_with_retry_after(real_redis_client):
    with patch("app.rate_limit.settings") as mock_settings:
        mock_settings.general_rate_limit_max_requests = 3
        mock_settings.general_rate_limit_window_seconds = 60
        middleware = GeneralRateLimitMiddleware(app=None)
        for _ in range(3):
            response = await middleware.dispatch(_fake_request("/fleet/hub-1/drivers"), _call_next)
            assert response.status_code == 200
        response = await middleware.dispatch(_fake_request("/fleet/hub-1/drivers"), _call_next)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"


async def test_different_ips_get_independent_budgets(real_redis_client):
    with patch("app.rate_limit.settings") as mock_settings:
        mock_settings.general_rate_limit_max_requests = 1
        mock_settings.general_rate_limit_window_seconds = 60
        middleware = GeneralRateLimitMiddleware(app=None)

        def _request_from(ip):
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            return Request(
                {
                    "type": "http", "method": "GET", "path": "/fleet/hub-1/drivers",
                    "query_string": b"", "headers": [], "scheme": "http",
                    "client": (ip, 12345), "server": ("testserver", 80),
                },
                receive,
            )

        first_ip_first = await middleware.dispatch(_request_from("9.9.9.1"), _call_next)
        second_ip_first = await middleware.dispatch(_request_from("9.9.9.2"), _call_next)
        first_ip_second = await middleware.dispatch(_request_from("9.9.9.1"), _call_next)

    assert first_ip_first.status_code == 200
    assert second_ip_first.status_code == 200  # a different IP's own budget, unaffected by the first
    assert first_ip_second.status_code == 429  # first IP's own second request, over its cap of 1


def test_rate_limit_key_is_scoped_per_ip():
    assert _key("1.2.3.4") != _key("5.6.7.8")
