"""locations gain a node class, its source, and the evidence behind it

IDN-3 (docs/ROADMAP_1.5.md Phase 1).

Seven classes: shop, parts store, dealer, body shop, warehouse, transfer,
municipal. `PRD-1` is what needs them - batch value is +3-7% at high-frequency
shops against +195-271% at warehouse and transfer nodes, and a model that cannot
tell those apart is averaging across two orders of magnitude.

Three columns rather than one:

  - `node_class` is nullable, permanently. IDN-3's target is under 2%
    unlabelled, not zero, and a dock nobody can classify is a real state.
  - `node_class_source` separates a person's label from the classifier's guess,
    so an inferred label can never overwrite a human one.
  - `node_class_evidence` is the text the classifier matched. A reviewer
    correcting a label needs to see what it keyed on.

**Stored as a plain string, not a Postgres enum.** The seven classes are a
first cut at a taxonomy nobody has validated against the real accounts yet, and
an eighth is likely once somebody labels the founding set. Adding a value to an
enum is a migration and a deploy; adding one here is a constant. The check that
matters - that only the seven are written - lives in `set_node_class`, where a
reviewer gets a usable error, rather than in a constraint that surfaces as an
integrity violation three layers up.

No backfill: nothing can be classified until the accounts exist, and inventing
labels here would be exactly the invisible wrong answer this column's `source`
field exists to prevent. `classify_unlabelled_locations` is the job that fills
it, and `classification_coverage` is how the done-when gets measured.

Revision ID: 0048
Revises: 0047
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("locations", sa.Column("node_class", sa.String(length=24), nullable=True))
    op.add_column(
        "locations", sa.Column("node_class_source", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "locations", sa.Column("node_class_evidence", sa.String(length=64), nullable=True)
    )
    # PRD-1 groups by this, and the coverage query filters on it being set.
    op.create_index("ix_locations_node_class", "locations", ["node_class"])


def downgrade() -> None:
    """Drops every label, including the ones a person set by hand.

    The inferred ones are recoverable by re-running the classifier. The human
    corrections are not recoverable from anywhere - they existed only here.
    """
    op.drop_index("ix_locations_node_class", table_name="locations")
    op.drop_column("locations", "node_class_evidence")
    op.drop_column("locations", "node_class_source")
    op.drop_column("locations", "node_class")
