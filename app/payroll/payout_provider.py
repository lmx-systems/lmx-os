"""
Instant per-delivery payout for gig-classified drivers (docs/ROADMAP.md
A11) - same "unconfigured credential -> stub" pattern as
app/payroll/base.py's PayrollProvider, whose own docstring already names
Stripe Connect as the anticipated gig payout rail.

A separate interface from PayrollProvider, not a new method on it:
PayrollProvider.submit_hours submits *hours* for a pay period on a
recurring cycle; this pays a *fixed amount* for one already-completed
delivery, immediately - a fundamentally different shape and cadence, not
a variant of the same call.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class PayoutProvider(ABC):
    engine_name: str

    @abstractmethod
    async def pay_out(self, *, connected_account_id: str, amount_cents: int, description: str) -> str | None:
        """Pay a driver instantly for one completed delivery. Returns the
        provider's own reference id for the transfer (or None - e.g. the
        stub client, which has nothing real to reference)."""
        raise NotImplementedError
