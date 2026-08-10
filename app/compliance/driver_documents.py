"""
The one function that decides whether a driver may go on shift
(docs/ROADMAP.md R4).

**The rule this replaces was inverted.** It read: refuse if any document row on
file has passed its expiry date. Which sounds right, and is wrong twice over:

  - the date was written by the driver, so anyone could type a future one;
  - it only considered rows that EXISTED, so a driver with no documents at all had
    nothing expired and passed. It blocked the honest driver who recorded a lapsed
    license and cleared the one who recorded nothing.

The rule now reads: every required document must be **present, reviewed by an ops
user, and unexpired according to the date that reviewer read off it**. Absence is
a failure. An unreviewed upload is a failure. A rejection is a failure. Those are
distinct reasons and each is reported separately, because "we haven't looked at
your insurance yet" and "your license expired" need completely different actions
from completely different people.

**This is a presence check, not a safety check**, and the distinction is worth
keeping sharp: nothing here establishes that a license is genuine or that its
holder has a clean driving record. That is R2 (background checks and MVR
screening), and it needs a service and a policy, not code. What this module does is
stop the system asserting a check it never performed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver_document import (
    REQUIRED_DOC_TYPES,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    REVIEW_VERIFIED,
    DriverDocument,
)


@dataclass(frozen=True)
class DocumentProblem:
    doc_type: str
    # A machine-readable reason so the driver app can branch (upload something vs.
    # wait vs. contact ops) rather than parsing prose.
    reason: str  # missing | awaiting_review | rejected | expired
    detail: str


# Written for the driver reading them on their phone at 6am, wondering why the
# "go online" button won't work. Each says whose move it is next.
_MESSAGES = {
    "missing": "No {doc_type} on file - upload a photo of it to get started.",
    "awaiting_review": "Your {doc_type} is uploaded and waiting on an LMX review.",
    "rejected": "Your {doc_type} needs re-uploading. {reason}",
    "expired": "Your {doc_type} expired on {expires_at} - upload the renewed one.",
}


@dataclass(frozen=True)
class ComplianceResult:
    problems: list[DocumentProblem]

    @property
    def can_go_on_shift(self) -> bool:
        return not self.problems


async def evaluate_driver_documents(
    session: AsyncSession, driver_id: str, *, today: date | None = None
) -> ComplianceResult:
    """Every reason this driver may not go on shift, or an empty list.

    Returns ALL problems rather than the first: a driver with no license and an
    expired insurance certificate should be told both at once, not sent back three
    times.

    `today` is injectable because expiry is a date boundary, and a test that
    happens to run on the day a fixture expires would otherwise pass or fail
    depending on the clock.
    """
    on = today or date.today()

    result = await session.execute(
        select(DriverDocument).where(DriverDocument.driver_id == driver_id)
    )
    by_type = {doc.doc_type: doc for doc in result.scalars().all()}

    problems: list[DocumentProblem] = []
    for doc_type in REQUIRED_DOC_TYPES:
        doc = by_type.get(doc_type)

        if doc is None or doc.file_url is None:
            # No row, or a row with nothing uploaded against it. Both mean we hold
            # no evidence, and this is the case the old gate scored as a pass.
            problems.append(
                DocumentProblem(
                    doc_type=doc_type,
                    reason="missing",
                    detail=_MESSAGES["missing"].format(doc_type=doc_type),
                )
            )
            continue

        if doc.review_status == REVIEW_REJECTED:
            problems.append(
                DocumentProblem(
                    doc_type=doc_type,
                    reason="rejected",
                    detail=_MESSAGES["rejected"].format(
                        doc_type=doc_type, reason=doc.rejection_reason or ""
                    ).strip(),
                )
            )
            continue

        if doc.review_status == REVIEW_PENDING or doc.verified_expires_at is None:
            # Uploaded but unverified. Deliberately NOT a pass: treating an
            # unreviewed document as good is the same self-attestation this work
            # exists to remove, just moved one step later.
            problems.append(
                DocumentProblem(
                    doc_type=doc_type,
                    reason="awaiting_review",
                    detail=_MESSAGES["awaiting_review"].format(doc_type=doc_type),
                )
            )
            continue

        if doc.review_status == REVIEW_VERIFIED and doc.verified_expires_at < on:
            problems.append(
                DocumentProblem(
                    doc_type=doc_type,
                    reason="expired",
                    detail=_MESSAGES["expired"].format(
                        doc_type=doc_type, expires_at=doc.verified_expires_at.isoformat()
                    ),
                )
            )

    return ComplianceResult(problems=problems)
