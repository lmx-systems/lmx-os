"""The record of one dock being declared the same place as another.

`docs/ROADMAP_1.5.md` IDN-2, implementing §2.2(c): *"Every merge in the founding
~230 is proposed and confirmed by a person in one sitting. From then on,
auto-merge on normalised address with a review queue for conflicts only. Every
merge - automatic or confirmed - writes an audit record and is reversible."*

**One table is the queue and the audit log.** A proposal that is confirmed
becomes the audit record of the merge it authorised, rather than being copied
into a second table that then has to be kept in agreement with the first. The
`status` column is the whole state machine:

    proposed --confirm--> applied --revert--> reverted
        |         |
        |         +--> superseded   (already one dock; nothing to do)
        |
        +----reject-----> rejected

Nothing is ever deleted from this table. A rejected proposal is as much a
decision as a confirmed one - it is the record that a person looked at two docks
and said they are different places - and re-proposing the same pair should
surface that somebody already answered.

**Why `moved_shop_ids` is stored.** Merging B into A repoints B's shops at A.
Without recording exactly which shops moved, a revert cannot tell them apart
from shops that already pointed at A, and "reversible" would be a claim rather
than a fact. §2.2(c) asks for reversible, so the column exists.

**Why the shops are repointed at all**, rather than left pointing at B and
resolved through the alias chain at read time: every per-dock aggregate would
otherwise have to remember to follow the chain, and the first query that forgets
silently splits a dock back into two - which is the exact defect IDN-1 exists to
remove. Repointing makes the correct answer the default one.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

STATUS_PROPOSED = "proposed"
STATUS_APPLIED = "applied"
STATUS_REJECTED = "rejected"
STATUS_REVERTED = "reverted"
# Confirmed by a reviewer who was right - and nothing had to move, because an
# earlier merge had already brought these two together. Collapsing five records
# takes four merges, but a detector comparing every pair queues ten proposals,
# so six of them are true statements that change nothing.
#
# A distinct status rather than `applied`, because `revert` must not touch
# these: they moved no shops and set no alias, and reverting one would clear a
# merge it did not make.
STATUS_SUPERSEDED = "superseded"

# Who decided. §2.2(c) draws the line between the founding set, which a person
# confirms one at a time, and everything after it - so the record has to say
# which kind of decision this was, not just that a decision happened.
SOURCE_HUMAN = "human"
SOURCE_AUTOMATIC = "automatic"


class LocationMerge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "location_merges"

    # The dock being absorbed, and the dock it is absorbed into. Direction
    # matters and is not symmetric: `source` keeps its row and its address as an
    # alias, `target` is the one everything ends up pointing at.
    source_location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id"), nullable=False, index=True
    )
    target_location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_PROPOSED, nullable=False, index=True
    )

    # Why the pair was proposed, in words a person reviewing it can act on -
    # "addresses 0.94 similar", "same coordinates". A reviewer deciding whether
    # two records are one place needs to know what made the machine think so.
    reason: Mapped[str] = mapped_column(String(255), nullable=False)

    # Null until decided. `automatic` decisions have a source but no user.
    decision_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_by_ops_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ops_users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Exactly which shops this merge moved, so the revert moves back those and
    # only those. Empty list is meaningful: the merge applied and no shop had to
    # move, which is different from null.
    moved_shop_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    reverted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
