"""One write a person made from the console (`DEC-7`, decision D-SEAT).

The end state is nobody running the day (`docs/ROADMAP_1.5.md` §2.6), so the
number that says how far there is to go is how often a person still has to act:
**person touches per 100 orders**, by what they did. Each row is one successful
ops write, recorded by `app/playbook/touches.py` after the write went through.

`category` is what kind of work it was, from `TOUCH_CATEGORIES` there: running
the day, fixing the record, setting policy, or running the business. The first
two are what the playbook is meant to take over; the last two are counted so
that nothing a person does is invisible, but they are not the headline.
"""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPrimaryKeyMixin

CATEGORY_DAY = "day"
CATEGORY_RECORD = "record"
CATEGORY_POLICY = "policy"
CATEGORY_BUSINESS = "business"
CATEGORY_UNCLASSIFIED = "unclassified"
CATEGORIES = (CATEGORY_DAY, CATEGORY_RECORD, CATEGORY_POLICY, CATEGORY_BUSINESS, CATEGORY_UNCLASSIFIED)


class OpsTouch(Base, UUIDPrimaryKeyMixin):
    """Append-only: migration `0082` refuses UPDATE and DELETE.

    No foreign keys and no `TimestampMixin`, as `DispatchAssignment`: the record
    of a person having acted must outlive the order or the account it names.
    """

    __tablename__ = "ops_touches"
    __table_args__ = (
        CheckConstraint(
            "category IN ('day', 'record', 'policy', 'business', 'unclassified')",
            name="ck_ops_touches_category",
        ),
    )

    touched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ops_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # The route's name - the endpoint function, e.g. `resolve_order`.
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    # Which thing it was done to, as the path named it.
    path_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    hub_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
