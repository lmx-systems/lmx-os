"""Who answered each site fact, and answers that disagreed (DE-15)

`docs/ROADMAP_1.5.md` DE-15, after the review of #223. The door survey writes a
site's kind, setting and shared-address answer, and until now the last answer
to arrive won, with nothing kept of who gave it or of the answer it replaced. A
phone that answered offline from a stale copy of the site could overwrite a
newer answer from another driver, and a "no, nobody shares this address" could
silently replace the distributor's own record that it is shared.

The rule is now that an answer fills a fact only while it is open - unknown, or
only the desk's guess (`app/identity/site_facts.py`). An answer that disagrees
with what a person or the import already said is not applied: it is kept here
as a dispute beside the fact, for a person to settle.

`locations.site_fact_answers` is a JSON object, fact to
`{"value", "by", "at", "stop_id", "disputes": [...]}`: the answer that was
applied and who gave it, and each later answer that disagreed. Nothing is
backfilled - answers before this migration have a source and no answerer.

Revision ID: 0081
Revises: 0080
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0081"
down_revision: Union[str, None] = "0080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column("site_fact_answers", postgresql.JSONB(none_as_null=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("locations", "site_fact_answers")
