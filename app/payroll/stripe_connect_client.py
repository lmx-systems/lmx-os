"""
Real Stripe Connect client - see app/payroll/payout_provider.py's module
docstring for the overall interface/stub-fallback pattern.

Same caveat as app/payroll/rippling_client.py: no Stripe account/API
credentials exist yet, so the endpoint and payload below are a
best-effort interpretation of Stripe's published Transfers API
(https://stripe.com/docs/api/transfers), not verified against a real
account. Confirm against Stripe's actual API docs and a sandbox/test-mode
account before this is ever exercised for real.

Not the official `stripe` SDK - this codebase's third-party HTTP clients
(sms_client.py, rippling_client.py, voice_client.py) are all small
hand-rolled httpx clients, not vendor SDKs, so this matches that existing
style. Stripe's API authenticates via HTTP Basic auth with the secret key
as the username and an empty password, same as `curl -u sk_...: ...`.
"""
from __future__ import annotations

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.payroll.payout_provider import PayoutProvider

logger = structlog.get_logger(__name__)

STRIPE_TRANSFERS_ENDPOINT = "https://api.stripe.com/v1/transfers"


class StripeConnectPayoutProvider(PayoutProvider):
    engine_name = "stripe_connect"

    def __init__(self, secret_key: str) -> None:
        self._http = httpx.AsyncClient(timeout=5.0, auth=(secret_key, ""))

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.25, max=1))
    async def pay_out(self, *, connected_account_id: str, amount_cents: int, description: str) -> str | None:
        response = await self._http.post(
            STRIPE_TRANSFERS_ENDPOINT,
            data={
                "amount": amount_cents,
                "currency": "usd",
                "destination": connected_account_id,
                "description": description,
            },
        )
        response.raise_for_status()
        return response.json().get("id")
