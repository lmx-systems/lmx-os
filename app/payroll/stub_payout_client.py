"""Stub payout provider - see app/payroll/payout_provider.py's module docstring."""
from __future__ import annotations

import structlog

from app.payroll.payout_provider import PayoutProvider

logger = structlog.get_logger(__name__)


class StubPayoutProvider(PayoutProvider):
    engine_name = "stub"

    async def pay_out(self, *, connected_account_id: str, amount_cents: int, description: str) -> str | None:
        logger.info("stub_payout_sent", connected_account_id=connected_account_id, amount_cents=amount_cents)
        return None
