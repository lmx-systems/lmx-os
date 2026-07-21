"""
Payroll client abstraction (roadmap items B4/A9, provider: Rippling) -
stub behavior, unlinked-driver handling, client selection, and the real
client refusing to guess at Rippling's API.
"""
from unittest.mock import patch

import pytest

from app.payroll.client import (
    PayPeriodInput,
    RipplingClient,
    StubPayrollClient,
    get_payroll_client,
)


def _entry(driver_id: str = "d1", employee_id: str | None = "emp_123") -> PayPeriodInput:
    return PayPeriodInput(
        driver_id=driver_id,
        payroll_employee_id=employee_id,
        period_start="2026-07-13",
        period_end="2026-07-20",
        hours_worked=32.5,
        completed_drops=81,
    )


@pytest.mark.asyncio
async def test_stub_submits_linked_and_skips_unlinked_drivers():
    client = StubPayrollClient()
    result = await client.submit_pay_period([_entry("d1", "emp_1"), _entry("d2", None)])
    assert [e.driver_id for e in result.submitted] == ["d1"]
    assert [e.driver_id for e in result.skipped_unlinked] == ["d2"]
    assert result.provider == "stub"


def test_unconfigured_token_selects_stub():
    with patch("app.payroll.client.settings") as mock_settings:
        mock_settings.rippling_api_token = None
        assert isinstance(get_payroll_client(), StubPayrollClient)


def test_configured_token_selects_rippling_client():
    with patch("app.payroll.client.settings") as mock_settings:
        mock_settings.rippling_api_token = "tok_test"
        mock_settings.rippling_api_base_url = "https://sandbox.rippling.example"
        client = get_payroll_client()
        assert isinstance(client, RipplingClient)


@pytest.mark.asyncio
async def test_rippling_client_refuses_to_run_as_a_skeleton():
    # Deliberate: failing loudly beats silently guessing Rippling's
    # pay-input payload shape - see app/payroll/client.py's docstring.
    client = RipplingClient("tok_test", "https://api.rippling.com")
    with pytest.raises(NotImplementedError):
        await client.submit_pay_period([_entry()])
