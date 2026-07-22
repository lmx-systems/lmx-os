"""
A generated billing statement for a client covering a date range
(docs/ROADMAP.md C3). Created once, by app/billing/service.py's
generate_invoice() - see that module for how orders get attached to one.

invoice_number is a separate, human-facing sequence (not the UUID PK) -
the kind of number a client would actually reference when asking about a
bill, starting from a real-looking value rather than 1 (see the
migration). Payment collection (a status field, a paid_at timestamp, a
processor reference) is explicitly out of scope for this pass - see this
model's absence of any such field, and docs/ROADMAP.md C3's note on why.
"""
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Invoice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "invoices"

    client_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id"), nullable=False)
    # server_default must be declared here, matching the migration's
    # nextval('invoice_number_seq') default, not just in the migration -
    # otherwise SQLAlchemy has no way to know this column should be
    # omitted from the INSERT (and fetched back afterward) rather than
    # sent as an explicit NULL, which would violate the NOT NULL
    # constraint the very first time a row is ever inserted through the ORM.
    invoice_number: Mapped[int] = mapped_column(
        Integer, server_default=text("nextval('invoice_number_seq')"), nullable=False, unique=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
