"""dispatch_assignments: who gave an order to a driver by hand

The exceptions queue's "nobody has it" row told a dispatcher to "assign it or
put it back in the queue" and offered neither. `POST /admin/orders/{id}/assign`
is the assign; this is its record.

One row per assign: the order, the driver, the offer they were sent (an idle
driver, who may decline it), and the ops user who chose. The decision log
records why the optimizer picked a driver; until this, a driver picked by a
person would have left no trace that a person picked them, and a manual offer
declined would have counted against the optimizer's acceptance figures. Adding
to a working driver's route by hand is not built yet (`CON-5`); when it is, this
table gains a route column beside the offer.

**Its own table, not `dispatcher_overrides`.** That table's CHECK allows only
`release` and `hold` and requires a reason code; it records a disagreement with
the hold queue's timing. An assign overrules the optimizer's choice of driver
and asks no reason (the default chosen for this PR, to be overruled if the
founder wants one).

**Append-only**, the same trigger shape as `0060`. No foreign keys, for the
reason `0051`, `0053`, `0054` and `0060` give: the record of who did something
must stay readable if the order, the driver or the account is purged.

Revision ID: 0078
Revises: 0077
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078"
down_revision: Union[str, None] = "0077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION dispatch_assignments_are_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'dispatch_assignments is append-only: % on row % was rejected. '
        'Who gave an order to a driver is a record of a moment, not a setting.',
        TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "dispatch_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("driver_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ops_user_id", sa.String(length=64), nullable=False),
        sa.Column("ops_user_email", sa.String(length=255), nullable=False),
    )
    op.create_index("ix_dispatch_assignments_hub_id", "dispatch_assignments", ["hub_id"])
    op.create_index("ix_dispatch_assignments_order_id", "dispatch_assignments", ["order_id"])

    op.execute(_IMMUTABLE_FN)
    op.execute(
        """
        CREATE TRIGGER dispatch_assignments_no_update_or_delete
        BEFORE UPDATE OR DELETE ON dispatch_assignments
        FOR EACH ROW EXECUTE FUNCTION dispatch_assignments_are_immutable();
        """
    )


def downgrade() -> None:
    """Drops every record of a manual assign.

    The orders and offers keep what the assigns did to them; who chose
    each driver, and that a person rather than the optimizer did, is gone.
    """
    op.execute(
        "DROP TRIGGER IF EXISTS dispatch_assignments_no_update_or_delete "
        "ON dispatch_assignments"
    )
    op.execute("DROP FUNCTION IF EXISTS dispatch_assignments_are_immutable()")
    op.drop_index("ix_dispatch_assignments_order_id", table_name="dispatch_assignments")
    op.drop_index("ix_dispatch_assignments_hub_id", table_name="dispatch_assignments")
    op.drop_table("dispatch_assignments")
