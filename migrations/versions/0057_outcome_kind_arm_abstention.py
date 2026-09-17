"""Let the ledger record that we did nothing on purpose (`EXP-3`)

`app/experiment/integrity.py` shipped with a limitation it reported on every
run: nothing records that dispatch declined to act on a control-arm order, so an
order genuinely dispatched the customer's old way is indistinguishable from one
we quietly held and batched. That is the contamination that most threatens a
savings claim, and it was undetectable.

This widens `outcome_ledger.kind` to hold the abstention. Second widening in
three migrations, which is worth a word: the constraint is what stops a typo
becoming a new outcome type nothing queries, and on an append-only table a typo
cannot be fixed by an UPDATE afterwards. Paying a migration each time is the
cost of that, and it is the right way round.

Revision ID: 0057
Revises: 0056
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0057"
down_revision: Union[str, None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "kind IN ('delivered', 'failed', 'dwell', 'disputed', 'cost')"
_NEW = "kind IN ('delivered', 'failed', 'dwell', 'disputed', 'cost', 'arm_abstention')"


def upgrade() -> None:
    op.drop_constraint("ck_outcome_ledger_kind", "outcome_ledger", type_="check")
    op.create_check_constraint("ck_outcome_ledger_kind", "outcome_ledger", _NEW)


def downgrade() -> None:
    """Fails if any abstention exists, which is correct.

    The rows cannot be deleted - the ledger refuses DELETE by trigger - and each
    one is the only evidence that a control order was left alone. Reverting on a
    database that has recorded them means deciding the arm can no longer be
    audited, which is a decision for a person.
    """
    op.drop_constraint("ck_outcome_ledger_kind", "outcome_ledger", type_="check")
    op.create_check_constraint("ck_outcome_ledger_kind", "outcome_ledger", _OLD)
