"""
Smoke test: the FastAPI app must import and register all Phase 1 routes
without needing a live Postgres/Redis connection (those are only touched
inside the lifespan handler on real startup, not at import time).
"""
from app.main import app


def test_app_imports_and_exposes_expected_routes():
    schema = app.openapi()
    expected_paths = {
        "/health",
        "/fleet/{hub_id}/drivers/state",
        "/fleet/{hub_id}/drivers/location",
        "/optimizer/{hub_id}/run-cycle",
        "/ingestion/{hub_id}/{client_id}/{source_system}",
    }
    assert expected_paths.issubset(schema["paths"].keys())
