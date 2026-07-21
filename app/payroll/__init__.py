from app.config import settings
from app.payroll.base import PayrollProvider
from app.payroll.rippling_client import RipplingPayrollProvider
from app.payroll.stub_client import StubPayrollProvider

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


__all__ = ["PayrollProvider", "get_payroll_provider"]
