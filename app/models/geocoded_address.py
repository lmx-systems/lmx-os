"""
One address we have tried to resolve to coordinates (docs/LMX_LINK_PLAN.md §1.2:
"geocode once on first order per address, cache and reuse").

This table is what makes a rate-limited, no-account geocoder viable. Without it,
request volume is orders-per-day; with it, volume is *new-addresses*-per-day, and
§2.2 principle 3 is explicit that those are wildly different numbers - "most
distributors deliver to the same 40-80 shops forever."

FAILURES ARE CACHED TOO, which is the non-obvious part. `lat`/`lng` null means we
asked and the address did not resolve. Storing that matters because the realistic
failure case is a typo the customer then retries: without a negative entry, three
attempts at the same bad address burn three of a very small number of requests
per second. `attempted_at` is what a future retry policy would key off - there
isn't one yet, and a permanently-cached failure for an address that later becomes
valid is the known cost of that.

Keyed on the *normalized* address (app/geocoding/base.py::normalize_address),
which is deliberately conservative: it case-folds and collapses whitespace but
does not canonicalize "St" vs "Street", because merging two addresses that differ
in a house number would send a driver next door. Under-normalizing costs a
duplicate row; over-normalizing costs a wrong delivery.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class GeocodedAddress(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "geocoded_addresses"

    # The cache key. Unique, so a concurrent double-geocode of the same address
    # is a conflict to resolve rather than two rows that could disagree.
    normalized_address: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    # Exactly what someone typed, kept alongside the key. Useful when a
    # resolution looks wrong and the question is whether the input or the
    # provider was at fault.
    raw_address: Mapped[str] = mapped_column(String(255), nullable=False)

    # Null together when the address did not resolve. See the module docstring:
    # a cached failure is a deliberate record, not a missing row.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    # The provider's own canonical form. Worth storing to show a user for
    # confirmation - "did you mean this?" - rather than echoing their input back.
    display_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Which provider answered. Recorded so a later switch to a keyed provider can
    # tell which rows came from the pilot geocoder and might warrant re-resolving.
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)

    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def resolved(self) -> bool:
        return self.lat is not None and self.lng is not None
