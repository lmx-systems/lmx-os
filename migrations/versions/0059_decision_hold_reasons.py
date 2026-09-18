"""decision_snapshots.hold_decisions: why each order was held (`AGT-4`)

`run_hold_cycle` returns a reason for every order it looks at - hot shot,
deadline reached, no cluster mate, no driver, conflict with a more urgent order -
and `run_cycle` kept only the set of ids it released. So the system held orders
and recorded no reason for any of it, in a product whose central claim is that
the hold is the product.

`AGT-4` is what surfaced it. Its done-when is "every explanation cites REC-1's
decision log rather than narrating. No explanation the record cannot support" -
and the only honest explanation of a hold was that we did not know.

Stored beside `assignments` rather than inside `inputs`, because it is an output.
Putting it in the frozen inputs would make the replay hash depend on what the
cycle concluded rather than on what it saw, which is the one thing `REC-1`'s
hash is for.

Defaults to an empty list, so snapshots written before this read as "no reasons
recorded" rather than as a cycle that held nothing. Not backfilled: the reasons
were never captured and inventing them would be the opposite of the point.

Revision ID: 0059
Revises: 0058
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059"
down_revision: Union[str, None] = "0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "decision_snapshots",
        sa.Column(
            "hold_decisions",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    """Drops every recorded hold reason.

    `decision_snapshots` refuses UPDATE and DELETE by trigger, so these rows
    cannot be rewritten - but a dropped column takes them with it, and they are
    the only record of why any order waited. AGT-4 goes back to being unable to
    answer its own question.
    """
    op.drop_column("decision_snapshots", "hold_decisions")
