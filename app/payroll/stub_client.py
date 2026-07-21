"""Stub payroll provider - see app/payroll/base.py's module docstring."""
from datetime import date

import structlog

from app.payroll.base import PayrollProvider

logger = structlog.get_logger(__name__)


class StubPayrollProvider(PayrollProvider):
    engine_name = "stub"

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
        logger.info(
            "stub_payroll_hours_submitted",
            driver_id=driver_id,
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            hours_worked=hours_worked,
            rate_cents=rate_cents,
        )
        return None
