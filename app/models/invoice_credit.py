"""
One SLA-breach credit on a statement (docs/ROADMAP.md W3, story DO-3).

**A breach costs nothing today** - C3's billing sums delivered orders and stops there, so a
delivery three hours late bills identically to one on time. This is the line that makes the
contract real.

A row per breached order rather than one aggregate credit, because a client asking "which
ones?" is the first question, and an aggregate answers it with "check your own records".
Each row carries what was promised, what happened, and how late - so the line is arguable
with, which is what a credit has to be.

Stored at generation time and never recomputed. If a client's terms change next quarter,
last quarter's statement must not quietly change with them.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class InvoiceCredit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "invoice_credits"

    invoice_id: Mapped[UUID] = mapped_column(
        ForeignKey("invoices.id"), nullable=False, index=True
    )
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    sla_tier: Mapped[str] = mapped_column(String(16), nullable=False)

    # Positive. Subtracted at the invoice level rather than stored negative, so nothing
    # downstream can add a credit by accident.
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)

    # The evidence, frozen. A credit a client cannot check is one they will ring up about.
    promised_by: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    minutes_late: Mapped[int] = mapped_column(Integer, nullable=False)
