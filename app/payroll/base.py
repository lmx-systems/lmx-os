"""
Payroll/payout provider interface (foundational piece of the W2 -> 1099 ->
gig phased rollout - see docs/NEXT_STEPS.md). One abstraction sits behind
however many actual pay rails this ends up needing: a W2 payroll run
today, a 1099 contractor-payment rail tomorrow, an instant-payout rail
(e.g. Stripe Connect) for gig work later. Callers submit hours for a pay
period; the provider is responsible for whatever happens next on its own
side (withholding, net pay, direct deposit) - this system is not, and
does not try to be, a payroll engine itself.

Same "unconfigured third-party credential -> stub mode" pattern as
app/messaging/sms_client.py and app/optimizer/google_routes_client.py:
RipplingPayrollProvider is the real implementation, used once Rippling
credentials are configured (none exist yet). Until then,
StubPayrollProvider logs the submission and returns no provider
reference, so the rest of the earnings/timesheet feature is buildable
and testable without a live Rippling account.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date


class PayrollProvider(ABC):
    engine_name: str

    @abstractmethod
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
        """Submit one driver's worked hours for a pay period. Returns the
        provider's own reference id for the submission (or None - e.g. the
        stub client, which has nothing real to reference)."""
        raise NotImplementedError
