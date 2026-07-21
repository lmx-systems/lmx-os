"""
Payroll provider abstraction (roadmap items B4/A9 - provider: Rippling).

Same "unconfigured -> stub" pattern as app/messaging/sms_client.py: with
no RIPPLING_API_TOKEN configured, every submission goes through
StubPayrollClient, which logs and records instead of sending - the rest
of the pipeline (pay-period assembly, export job) is fully runnable and
testable end-to-end without a Rippling account.

IMPORTANT - the real client is a deliberate skeleton, not a finished
integration. Two things must land before it can be completed:
1. A Rippling account with API access + sandbox (docs/ROADMAP.md B4's
   remaining sub-items). Rippling's write path for pay inputs (hours /
   earning amounts) is plan- and module-dependent - the exact endpoint,
   payload shape, and auth scopes must be confirmed against their live
   developer docs once we have credentials. Do NOT guess them in.
2. The pay formula decision (per-drop, hourly, or blend) - until then
   the export assembles BOTH inputs (hours and completed drops) per
   driver so either formula can be wired without re-plumbing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class PayPeriodInput:
    """One driver's pay inputs for one pay period - everything either pay
    formula (hourly or per-drop) would need."""

    driver_id: str
    payroll_employee_id: str | None
    period_start: str  # ISO date
    period_end: str  # ISO date
    hours_worked: float
    completed_drops: int


@dataclass
class PayrollSubmissionResult:
    submitted: list[PayPeriodInput] = field(default_factory=list)
    skipped_unlinked: list[PayPeriodInput] = field(default_factory=list)
    provider: str = "stub"


class BasePayrollClient(ABC):
    provider: str

    @abstractmethod
    async def submit_pay_period(self, inputs: list[PayPeriodInput]) -> PayrollSubmissionResult:
        """Submit one pay period's inputs for a set of drivers."""
        raise NotImplementedError


class StubPayrollClient(BasePayrollClient):
    """No Rippling credentials configured - log what would be sent, skip
    drivers with no payroll_employee_id link, deliver nothing."""

    provider = "stub"

    async def submit_pay_period(self, inputs: list[PayPeriodInput]) -> PayrollSubmissionResult:
        result = PayrollSubmissionResult(provider=self.provider)
        for entry in inputs:
            if not entry.payroll_employee_id:
                logger.warning("payroll_driver_not_linked", driver_id=entry.driver_id)
                result.skipped_unlinked.append(entry)
                continue
            logger.info(
                "payroll_submission_stubbed",
                driver_id=entry.driver_id,
                payroll_employee_id=entry.payroll_employee_id,
                hours_worked=entry.hours_worked,
                completed_drops=entry.completed_drops,
                period_start=entry.period_start,
                period_end=entry.period_end,
            )
            result.submitted.append(entry)
        return result


class RipplingClient(BasePayrollClient):
    """Skeleton for the real Rippling integration - see module docstring
    for the two prerequisites before this can be completed. Instantiated
    only when RIPPLING_API_TOKEN is configured."""

    provider = "rippling"

    def __init__(self, api_token: str, base_url: str) -> None:
        self._api_token = api_token
        self._base_url = base_url.rstrip("/")

    async def submit_pay_period(self, inputs: list[PayPeriodInput]) -> PayrollSubmissionResult:
        # Deliberately unimplemented rather than guessed: Rippling's pay-input
        # write API (endpoint, payload, scopes) must be verified against
        # their developer docs with real sandbox credentials first. Failing
        # loudly here beats silently submitting a wrong payload to payroll.
        raise NotImplementedError(
            "RipplingClient.submit_pay_period is a skeleton - verify Rippling's "
            "pay-input API against sandbox credentials before implementing "
            "(docs/ROADMAP.md item B4's remaining sub-items)."
        )


def get_payroll_client() -> BasePayrollClient:
    if settings.rippling_api_token:
        return RipplingClient(settings.rippling_api_token, settings.rippling_api_base_url)
    return StubPayrollClient()
