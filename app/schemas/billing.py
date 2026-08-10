"""
Schemas for generated invoices (docs/ROADMAP.md C3, app/billing/service.py)
- shared as-is between the admin-triggered generation endpoint
(app/api/admin_routes.py) and the client-portal's read-only view
(app/api/client_routes.py). An invoice looks the same to both; only
who's allowed to see which invoice differs, enforced at the route level,
not by exposing different fields per audience.
"""
from datetime import date

from pydantic import BaseModel


class InvoiceGenerateBody(BaseModel):
    period_start: date
    period_end: date  # exclusive - see app/billing/service.py's generate_invoice() docstring


class InvoiceLineItem(BaseModel):
    order_id: str
    external_order_ref: str
    shop_name: str | None
    sla_tier: str | None
    delivered_at: str | None
    fee_cents: int
    # How that fee was arrived at (docs/ROADMAP.md F5). With a flat per-drop rate the
    # question never came up; with a rate table, "why is this line $19.40" is one a client
    # will ask, and the answer has to travel with the line rather than being reconstructed
    # from a rate card that may since have changed.
    fee_breakdown: dict | None = None


class InvoiceCreditLine(BaseModel):
    """One SLA-breach credit (docs/ROADMAP.md W3, story DO-3).

    Carries the evidence - what was promised, what happened, how late - because a credit a
    client cannot check is one they will ring up about, and an aggregate "credits: $84"
    answers "which ones?" with "check your own records".
    """

    order_id: str
    sla_tier: str
    amount_cents: int
    reason: str
    promised_by: str
    delivered_at: str
    minutes_late: int


class InvoiceSummaryView(BaseModel):
    invoice_id: str
    invoice_number: int
    period_start: date
    period_end: date
    generated_at: str
    # What was charged, what was credited back, and what is owed. All three, because a
    # statement showing only the net is one a client cannot reconcile - and one that hid
    # the credit would also hide the fact that we missed something.
    gross_cents: int
    credit_cents: int
    total_cents: int
    order_count: int


class InvoiceDetailView(InvoiceSummaryView):
    line_items: list[InvoiceLineItem]
    credits: list[InvoiceCreditLine] = []
