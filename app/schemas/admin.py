"""Schemas for internal/admin-only endpoints (app/api/admin_routes.py)."""
from pydantic import BaseModel


class ShopOnboardingInput(BaseModel):
    name: str
    address: str
    lat: float
    lng: float
    external_ref: str
    phone: str | None = None


class RateOnboardingInput(BaseModel):
    sla_tier: str  # T1 | T2 | T3 | HOT_SHOT - not enum-validated, see ClientRate's docstring
    rate_per_drop_cents: int


class ClientOnboardingBody(BaseModel):
    """
    Minimal client onboarding (Phase 8) - creates a Client, its shop(s),
    its per-tier billing rates, and its portal login credentials in one
    action, since there's no admin UI yet to do this as separate steps.
    """

    hub_id: str
    name: str
    pos_system: str = "flat_file"
    shops: list[ShopOnboardingInput]
    rates: list[RateOnboardingInput]
    portal_email: str
    portal_password: str


class ClientOnboardingResult(BaseModel):
    client_id: str
    shop_ids: list[str]


class DriverPayrollSubmission(BaseModel):
    driver_id: str
    driver_name: str
    employment_type: str
    # w2 drivers are paid monthly, 1099/gig weekly (app/payroll/hours.py) -
    # per-submission, not per-run, since one hub can mix employment types
    # with different period lengths in the same payroll run.
    period_start: str
    period_end: str
    hours_worked: float
    overtime_hours: float
    estimated_pay_cents: int
    provider_reference: str | None = None


class PayrollRunResult(BaseModel):
    hub_id: str
    engine: str
    submissions: list[DriverPayrollSubmission]
