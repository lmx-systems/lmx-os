"""Who gave an order to a driver by hand (`CON-4`'s row action).

The optimizer chooses a driver and the decision log records why (`REC-1`). When
a dispatcher chooses instead - from the exceptions queue, for an order nobody
has - this is the record of it: which order, which driver, the offer they were
sent (which they can decline), and who decided. Adding to a route a driver is
already driving is not built yet (`CON-5`); when it is, this table gains the
route beside the offer.

Its own table rather than a row in `dispatcher_overrides`. An override overrules
the hold queue's *timing* and must carry a reason code (`CON-2`); an assign
overrules the optimizer's choice of *driver*, and no reason is asked for - the
default the PR names, for the founder to overrule. Mixing the two would put
rows with no reason into a table whose CHECK exists to refuse them.

What it makes possible: a manual offer stays separable from an optimizer one,
so a decline or a lapse on an offer a dispatcher made (I4's acceptance figures)
is not counted against the optimizer's choice.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPrimaryKeyMixin

class DispatchAssignment(Base, UUIDPrimaryKeyMixin):
    """One manual assign, append-only.

    No `TimestampMixin`, for the reason `DispatcherOverride` gives: nothing here
    may change, so an `updated_at` would be a field that never moves. The
    trigger in migration `0078` refuses UPDATE and DELETE.
    """

    __tablename__ = "dispatch_assignments"

    hub_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    driver_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # The offer the driver was sent. A value, not a foreign key, so the record
    # survives the offer being purged.
    offer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Copied as values so "who did this" survives the account being deleted.
    ops_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ops_user_email: Mapped[str] = mapped_column(String(255), nullable=False)
