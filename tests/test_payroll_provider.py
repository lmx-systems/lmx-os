"""Payroll provider selection - same "unconfigured credential -> stub"
pattern as tests/test_optimizer_google_client.py's client-selection tests."""
from unittest.mock import patch

from app.payroll import get_payroll_provider


def test_provider_selection_falls_back_to_stub_without_credentials():
    with patch("app.payroll.settings") as mock_settings:
        mock_settings.rippling_api_key = None
        mock_settings.rippling_base_url = None
        result = get_payroll_provider()
    assert result.engine_name == "stub"


def test_provider_selection_uses_rippling_when_credentials_set():
    with patch("app.payroll.settings") as mock_settings:
        mock_settings.rippling_api_key = "fake-key"
        mock_settings.rippling_base_url = "https://api.rippling.com"
        result = get_payroll_provider()
    assert result.engine_name == "rippling"
