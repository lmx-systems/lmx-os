"""
A driver's compliance document - license, insurance (profile screen 1r,
docs/ROADMAP.md R4).

**This table used to record a claim and present it as a check.** The driver hit
`PUT /driver/me/documents/{doc_type}` with a `file_url` (any string at all, never
uploaded anywhere) and an `expires_at` of their choosing, and
`update_my_availability` then refused to put them online if that self-chosen date
was in the past. Two holes fell out of that:

  1. **The date was the driver's to type.** A driver whose license expired last
     month typed next year and went online. The gate read their answer, not a
     document.
  2. **The gate only looked at rows that existed.** A driver with no documents at
     all had no expired ones, so they passed - meaning it blocked the honest
     driver who recorded a lapsed license and waved through the one who recorded
     nothing.

So the model now separates what the driver asserts from what LMX established:

  `claimed_expires_at`   what the driver typed. Never used by any gate.
  `verified_expires_at`  what an ops reviewer read off the actual document.
                         NULL until someone has looked. This is the only date any
                         decision may read.
  `review_status`        pending | verified | rejected.

Nothing here verifies that a license is genuine or that its holder is safe to put
behind the wheel - only a human reading the document, and eventually an MVR
service (R2), can do that. What this does is make the system stop claiming
otherwise: it now records *who* established a fact and *when*, so "this driver is
compliant" is an assertion someone made rather than one the driver made about
themselves.

`file_url` is now written only by the backend, from a key it minted for a
presigned upload (app/storage/document_upload_client.py) - a driver can no longer
hand us a URL to somewhere we've never stored anything.
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# The document types a driver must have on file before they can work. The gate
# requires every one of these to be present AND verified AND unexpired - "no row
# at all" is a failure, not a pass.
#
# Deliberately a closed set: `doc_type` arrives as a URL path segment, and it used
# to be stored verbatim, so a driver could create documents of any type they
# invented. Junk types were harmless to the old gate only because it merely looked
# for expired rows; against a gate that checks for presence they would be a way to
# clutter the record.
REQUIRED_DOC_TYPES = ("license", "insurance")

REVIEW_PENDING = "pending"
REVIEW_VERIFIED = "verified"
REVIEW_REJECTED = "rejected"
REVIEW_STATUSES = (REVIEW_PENDING, REVIEW_VERIFIED, REVIEW_REJECTED)


class DriverDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "driver_documents"

    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # One of REQUIRED_DOC_TYPES, validated at the endpoint.

    # What the driver told us. Retained because it is useful context for the
    # reviewer ("they think this runs to March") and because a mismatch between
    # this and what the document actually says is itself worth seeing. **Never
    # read by a gate** - the name says so on purpose, so that a future caller
    # reaching for "the expiry date" cannot accidentally pick the unverified one.
    claimed_expires_at: Mapped[date] = mapped_column(Date, nullable=False)

    # What an ops reviewer read off the document. NULL until reviewed. The only
    # expiry any decision may act on.
    verified_expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=REVIEW_PENDING
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who established this. An unattributed compliance decision is not much better
    # than no decision - if a driver turns out to have been cleared on a bad
    # document, the question "who cleared it" has to have an answer.
    reviewed_by_ops_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ops_users.id"), nullable=True
    )
    # Shown to the driver so a rejection is actionable ("photo is cut off",
    # "this is the wrong side of the card") rather than a dead end.
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Set by the backend from a key it minted for a presigned upload - never from
    # a client-supplied string. NULL means the driver has started a record but not
    # yet uploaded anything, which is a distinct state from "uploaded and awaiting
    # review" and the reviewer needs to be able to tell them apart.
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def is_usable_on(self) -> bool:
        """Whether this document supports putting the driver on the road today."""
        return (
            self.review_status == REVIEW_VERIFIED
            and self.verified_expires_at is not None
            and self.verified_expires_at >= date.today()
        )
