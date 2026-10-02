"""The browser's preflight allows every method the front ends send.

The console and the portal run on other origins than the API, so a request that
isn't a simple GET or POST is preceded by a CORS preflight. The middleware
allowed only GET and POST, and Starlette answers a preflight for anything else
with a 400. In a browser that broke every PUT, PATCH and DELETE: editing a
client's rates and SLA terms, turning off or deleting an urgency rule, saving hub
settings, removing a closure, revoking a driver's device, and a client admin's
user edits. The same requests worked from curl, and nothing tested CORS.

The methods come from the front ends' own source, so a new one is covered the
day it is written.
"""
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
FRONT_ENDS = ("dashboard/src", "client-portal/src")


def _methods_the_front_ends_send() -> set[str]:
    methods = set()
    for folder in FRONT_ENDS:
        for path in (ROOT / folder).rglob("*.ts*"):
            methods |= set(
                re.findall(r"method:\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]", path.read_text())
            )
    return methods


def test_the_scan_finds_the_methods_that_broke():
    """If this fails the scan is reading the wrong thing, not the code."""
    assert {"PUT", "PATCH", "DELETE"} <= _methods_the_front_ends_send()


@pytest.mark.parametrize("method", sorted(_methods_the_front_ends_send() | {"GET", "POST"}))
async def test_a_browser_preflight_is_allowed_for(method):
    origin = settings.dashboard_cors_origin_list[0]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test") as client:
        response = await client.options(
            "/admin/hubs/00000000-0000-0000-0000-000000000000",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )
    assert response.status_code == 200, response.text
    assert method in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-origin"] == origin


async def test_an_origin_that_is_not_ours_is_still_refused():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api.test") as client:
        response = await client.options(
            "/admin/hubs/00000000-0000-0000-0000-000000000000",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "DELETE"},
        )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
