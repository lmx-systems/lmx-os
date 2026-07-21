"""
Real Rippling client - see app/payroll/base.py's module docstring for the
overall interface/stub-fallback pattern.

Same caveat as app/ingestion/epicor_adapter.py's field-name guesses: no
Rippling account/API credentials exist yet (docs/NEXT_STEPS.md), so the
endpoint path and payload shape below are a best-effort interpretation of
Rippling's published time-tracking API, not verified against a real
tenant. Confirm both against Rippling's actual API docs and a sandbox
account before this is ever exercised for real - the same "Phase 1
priority integration, unverified" status as the Epicor adapter.
"""
from __future__ import annotations

from datetime import date

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.payroll.base import PayrollProvider

logger = structlog.get_logger(__name__)

RIPPLING_TIME_ENTRIES_PATH = "/platform/api/time_entries"


class RipplingPayrollProvider(PayrollProvider):
    engine_name = "rippling"

    def __init__(self, api_key: str, base_url: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=5.0,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.25, max=1))
    async def submit_hours(
        self,
        *,
        driver_id: str,
        driver_name: str,
        period_start: date,
        period_end: date,
        hours_worked: float,
        rate_cents: int,
    ) -> str | None:
        response = await self._http.post(
            RIPPLING_TIME_ENTRIES_PATH,
            json={
                "worker_external_id": driver_id,
                "worker_name": driver_name,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "hours": hours_worked,
                "hourly_rate_cents": rate_cents,
            },
        )
        response.raise_for_status()
        return response.json().get("id")
