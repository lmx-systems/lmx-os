"""Let the outcome ledger hold a cost (`REC-2`)

`app/record/cost.py` computes what a drop actually cost and writes it into the
ledger. The ledger's `kind` is constrained at the database to the four kinds that
existed when `0053` created it, so the fifth needs letting in.

Widening a CHECK rather than dropping it: the constraint is what stops a typo
becoming a new outcome type that nothing queries and nobody notices, which on an
append-only table cannot be corrected by an UPDATE afterwards.

The table's append-only triggers are untouched. Replacing a constraint is DDL and
the triggers fire on UPDATE and DELETE of rows, so no existing entry is read,
rewritten or moved by this.

Revision ID: 0056
Revises: 0055
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "kind IN ('delivered', 'failed', 'dwell', 'disputed')"
_NEW = "kind IN ('delivered', 'failed', 'dwell', 'disputed', 'cost')"


def upgrade() -> None:
    op.drop_constraint("ck_outcome_ledger_kind", "outcome_ledger", type_="check")
    op.create_check_constraint("ck_outcome_ledger_kind", "outcome_ledger", _NEW)


def downgrade() -> None:
    """Narrows the constraint back, and will fail if any cost entry exists.

    That failure is correct and should not be worked around by deleting them:
    the rows cannot be deleted anyway - `outcome_ledger` refuses DELETE by
    trigger - and a cost entry is the only record of what a drop cost. Reverting
    this migration on a database that has recorded costs means deciding to lose
    them, which is a decision for a person rather than a downgrade script.
    """
    op.drop_constraint("ck_outcome_ledger_kind", "outcome_ledger", type_="check")
    op.create_check_constraint("ck_outcome_ledger_kind", "outcome_ledger", _OLD)
