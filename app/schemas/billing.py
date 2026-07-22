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


class InvoiceSummaryView(BaseModel):
    invoice_id: str
    invoice_number: int
    period_start: date
    period_end: date
    generated_at: str
    total_cents: int
    order_count: int


class InvoiceDetailView(InvoiceSummaryView):
    line_items: list[InvoiceLineItem]
