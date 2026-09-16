"""linkage_flags: two pieces of work that should have been one

REC-4 (docs/ROADMAP_1.5.md Phase 1). Three ways the system can hold two facts
separately and never join them: a return waiting at a dock we are about to
visit, one reference ordered by two branches of an account, and the same dock
visited twice in a day on separate trips.

The field logs are the argument for it - an $82 core part missed on two visits
and never collected, three returns written off in one day with no reschedule.
None of those was a bug. Every record involved was correct; nothing put them
side by side.

**Unique on (kind, fingerprint), which is what makes it a queue rather than a
firehose.** The detectors are meant to run repeatedly, and one that re-raises
the same pair every cycle produces a list people stop reading - the failure
IDN-2's merge queue was tuned to avoid. The fingerprint is built from the ids
involved, sorted, so the same observation made twice is one row.

**`resolved_at` rather than deletion.** A dismissed flag is evidence that a
person considered the case and said it was fine, which is worth keeping; and
deleting it would let the detector ask again tomorrow.

No foreign keys, same reasoning as 0051: a flag is an observation about a
moment, and it should stay readable if an order is later purged. The ids live
in `subjects` as values.

Revision ID: 0052
Revises: 0051
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "linkage_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("subjects", postgresql.JSONB(), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("kind", "fingerprint", name="uq_linkage_flags_kind_fingerprint"),
        sa.CheckConstraint(
            "kind IN ('open_return_on_a_visit', 'duplicate_across_branches', "
            "'repeat_visit_same_day')",
            name="ck_linkage_flags_kind",
        ),
    )
    op.create_index("ix_linkage_flags_hub_id", "linkage_flags", ["hub_id"])
    op.create_index("ix_linkage_flags_kind", "linkage_flags", ["kind"])
    # The queue read: everything still open for a hub, oldest first.
    op.create_index("ix_linkage_flags_detected_at", "linkage_flags", ["detected_at"])


def downgrade() -> None:
    """Drops the flags and the record of which ones a person dismissed.

    The open ones are recoverable - the detectors rebuild them. The
    resolutions are not: "somebody looked at this and it was fine" exists
    only here, and losing it means asking the same questions again.
    """
    op.drop_index("ix_linkage_flags_detected_at", table_name="linkage_flags")
    op.drop_index("ix_linkage_flags_kind", table_name="linkage_flags")
    op.drop_index("ix_linkage_flags_hub_id", table_name="linkage_flags")
    op.drop_table("linkage_flags")
