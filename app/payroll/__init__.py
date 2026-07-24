from app.config import settings
from app.payroll.base import PayrollProvider
from app.payroll.payout_provider import PayoutProvider
from app.payroll.rippling_client import RipplingPayrollProvider
from app.payroll.stripe_connect_client import StripeConnectPayoutProvider
from app.payroll.stub_client import StubPayrollProvider
from app.payroll.stub_payout_client import StubPayoutProvider

import structlog

logger = structlog.get_logger(__name__)


def get_payroll_provider() -> PayrollProvider:
    if settings.rippling_api_key and settings.rippling_base_url:
        logger.info("payroll_provider_selected", engine="rippling")
        return RipplingPayrollProvider(api_key=settings.rippling_api_key, base_url=settings.rippling_base_url)
    logger.warning(
        "payroll_provider_selected",
        engine="stub",
        reason="RIPPLING_API_KEY/RIPPLING_BASE_URL not fully configured - running in stub mode",
    )
    return StubPayrollProvider()


def get_payout_provider() -> PayoutProvider:
    if settings.stripe_connect_secret_key:
        logger.info("payout_provider_selected", engine="stripe_connect")
        return StripeConnectPayoutProvider(secret_key=settings.stripe_connect_secret_key)
    logger.warning(
        "payout_provider_selected",
        engine="stub",
        reason="STRIPE_CONNECT_SECRET_KEY not configured - running in stub mode",
    )
    return StubPayoutProvider()


__all__ = ["PayrollProvider", "PayoutProvider", "get_payroll_provider", "get_payout_provider"]
