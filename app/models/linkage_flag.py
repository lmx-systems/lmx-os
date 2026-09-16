"""Two things that should have been one trip (`docs/ROADMAP_1.5.md` REC-4).

The waste this names is not a routing inefficiency - the optimizer is already
good at those. It is the waste that comes from the system not noticing that two
separate pieces of work are about the same place, the same part, or the same
day. The field logs are full of it: an $82 core part missed on two visits and
never collected, three returns written off in one day with no reschedule.

**Three kinds, each a different way of failing to join two facts:**

  `open_return_on_a_visit`  we are driving to a dock that has a return waiting,
                            and nobody put them together. A van goes there,
                            comes back empty, and the part is written off.
  `duplicate_across_branches` two branches of one account ordered the same
                            reference. Either it is a genuine double-order to
                            correct, or it is one delivery being paid for twice.
  `repeat_visit_same_day`   the same dock, twice in a day, on separate trips.
                            Sometimes unavoidable; often the second order
                            arrived while the first van was still loading.

**A flag is a question, not a verdict.** Every one of these has a legitimate
explanation - a genuinely urgent second order, two branches that really did
both need the part. So a flag records what was noticed and why, and nothing
acts on one automatically. `resolved_at` exists so a dispatcher can say "looked
at it, it was fine" and not be asked again.

**Deduplicated by fingerprint.** The detectors are meant to run repeatedly -
after every cycle, or nightly over a window - and a queue that re-raises the
same pair every run is a queue people stop reading. That is the same failure
IDN-2's merge queue was tuned to avoid.
"""
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

KIND_OPEN_RETURN = "open_return_on_a_visit"
KIND_DUPLICATE_BRANCH = "duplicate_across_branches"
KIND_REPEAT_VISIT = "repeat_visit_same_day"

KINDS = (KIND_OPEN_RETURN, KIND_DUPLICATE_BRANCH, KIND_REPEAT_VISIT)


class LinkageFlag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "linkage_flags"
    __table_args__ = (
        UniqueConstraint("kind", "fingerprint", name="uq_linkage_flags_kind_fingerprint"),
    )

    hub_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Stable across runs and independent of row order, so the same observation
    # made twice is one flag. Built from the ids involved, sorted - see
    # `app/record/linkage.py`.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # The ids this flag is about. A dict rather than columns because the three
    # kinds relate different things - an order and a return, two orders, or a
    # dock and several orders - and three sets of mostly-null columns would say
    # less than one honest payload.
    subjects: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # What a dispatcher reads. Written to be actionable on its own: "a return
    # has been waiting at this dock since Tuesday" beats "open_return_on_a_visit".
    detail: Mapped[str] = mapped_column(String(500), nullable=False)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Set when somebody has looked. Null means still open; a note is optional
    # because forcing one makes people type "ok" rather than close the flag.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None
