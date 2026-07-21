"""
RateLimitMiddleware (roadmap item S5), exercised against a bare Starlette
app with fakeredis - same pattern as tests/test_api_auth.py for the
shared-secret middleware.
"""
from unittest.mock import patch

import pytest
from fakeredis import aioredis as fakeredis_aioredis
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import app.rate_limit as rate_limit_module
from app.rate_limit import RateLimitMiddleware


async def _ok(request):
    return PlainTextResponse("ok")


def _build_app() -> Starlette:
    routes = [Route("/health", _ok), Route("/orders/{hub_id}/summary", _ok)]
    test_app = Starlette(routes=routes)
    test_app.add_middleware(RateLimitMiddleware)
    return test_app


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rate_limit_module, "get_client", lambda: client)
    return client


def test_disabled_when_limit_is_zero(fake_redis):
    with patch("app.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_requests_per_minute = 0
        client = TestClient(_build_app())
        for _ in range(50):
            assert client.get("/orders/hub-1/summary").status_code == 200


def test_requests_within_budget_pass_then_429(fake_redis):
    with patch("app.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_requests_per_minute = 5
        client = TestClient(_build_app())
        for _ in range(5):
            assert client.get("/orders/hub-1/summary").status_code == 200
        over = client.get("/orders/hub-1/summary")
        assert over.status_code == 429
        assert over.headers["Retry-After"] == "60"


def test_health_is_exempt(fake_redis):
    with patch("app.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_requests_per_minute = 2
        client = TestClient(_build_app())
        for _ in range(10):
            assert client.get("/health").status_code == 200


def test_budget_is_per_ip(fake_redis):
    with patch("app.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_requests_per_minute = 3
        client = TestClient(_build_app())
        for _ in range(3):
            assert (
                client.get("/orders/hub-1/summary", headers={"X-Forwarded-For": "10.0.0.1"}).status_code
                == 200
            )
        assert client.get("/orders/hub-1/summary", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
        # A different caller still has its own budget.
        assert client.get("/orders/hub-1/summary", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200


def test_fails_open_when_redis_is_down(monkeypatch):
    class _BrokenRedis:
        def pipeline(self, transaction=True):
            raise ConnectionError("redis down")

    monkeypatch.setattr(rate_limit_module, "get_client", lambda: _BrokenRedis())
    with patch("app.rate_limit.settings") as mock_settings:
        mock_settings.rate_limit_requests_per_minute = 1
        client = TestClient(_build_app())
        # Way past the budget, but Redis is down - requests still succeed.
        for _ in range(5):
            assert client.get("/orders/hub-1/summary").status_code == 200
