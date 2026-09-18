"""dispatcher_overrides: when a human overruled the queue, and why (`CON-2`, `CON-3`)

`CON-2`'s done-when is *"no override completes without a reason"* and `CON-3`'s
is *"every override lands in the decision log as a labelled example"*. Building
them turned up the same thing `AGT-4` did one layer down: **there was no
override.** Nothing in `app/api/` let a dispatcher release a held order, hold a
released one, or overrule the queue in any way. The reason code had nothing to
be mandatory on.

So this is the override and its record in one table, and the three constraints
that make the done-whens structural rather than a convention.

**No override completes without a reason - at the database.** `reason_code` is
NOT NULL and checked against a closed vocabulary. A request missing it fails
validation in FastAPI, but that is the API's promise, not the system's: a script
writing this table directly must fail too, because the reason is what makes the
row worth keeping and a nullable column is an invitation to backfill one later
from memory.

**Append-only**, the same trigger shape as `0051`, `0053` and `0054`. An override
is somebody's judgement at a moment, recorded against the system's. Editing the
reason afterwards would let the label be tidied up once the outcome is known,
which is precisely the corruption that makes a training set worthless - and the
tidying would look like housekeeping.

**What the system had decided is copied, not referenced.** `system_action` and
`system_reason` hold the values from the decision snapshot as they read at the
time. `AGT-4` made those exist; this makes them a label. The pair - *the queue
said hold because no cluster mate, the dispatcher said release because the
customer called* - is the training example, and a foreign key would let it
change out from under the label or vanish with a purge.

**`system_decision_known` is the honest half.** When no cycle had recorded a
decision about the order, the override still happened and is still recorded, but
it is *not* a labelled disagreement: there is no system position to disagree
with. Marking it means the export for `M2` can exclude it rather than treating
an unknown as an implied hold. `MODEL_AND_DATA_BRIEF.md` §12 puts override
capture in Phase 3 under "live authority"; authority built on inferred labels is
the failure it is meant to prevent.

Revision ID: 0060
Revises: 0059
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Kept in step with `app/models/dispatcher_override.py::REASON_CODES` by a test.
# A vocabulary that drifts from the enum the UI offers would let a dispatcher
# pick a reason the database rejects, at the moment they are least able to
# absorb it.
_REASON_CODES = (
    "customer_called",
    "customer_waiting_on_site",
    "driver_going_that_way",
    "order_is_wrong",
    "hub_constraint",
    "system_looks_wrong",
    "other",
)

_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION dispatcher_overrides_are_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'dispatcher_overrides is append-only: % on row % was rejected. '
        'An override whose reason can be edited after the outcome is known '
        'is not a label.',
        TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    codes = ", ".join(f"'{code}'" for code in _REASON_CODES)
    op.create_table(
        "dispatcher_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        # No foreign keys, for the reason 0051, 0053 and 0054 already record: this
        # is evidence about a moment and must stay readable if the order, the hub
        # or the ops user is ever purged.
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("overridden_at", sa.DateTime(timezone=True), nullable=False),
        # Who. Copied as values so "who decided this" survives the account being
        # deleted - which is when somebody is most likely to be asking.
        sa.Column("ops_user_id", sa.String(length=64), nullable=False),
        sa.Column("ops_user_email", sa.String(length=255), nullable=False),
        # What the human did.
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        # Free text, optional, and never a substitute for the code. Searchable by
        # a person, not by a model.
        sa.Column("note", sa.Text(), nullable=True),
        # What the system had decided, as values. Null when it had not decided.
        sa.Column("system_action", sa.String(length=16), nullable=True),
        sa.Column("system_reason", sa.String(length=64), nullable=True),
        sa.Column("system_decision_known", sa.Boolean(), nullable=False),
        # Provenance, AGT-4 style: the snapshot the system's position was read
        # from. Nullable, because an override of a decision nobody recorded has
        # no snapshot to cite - and citing the most recent one anyway would be
        # the narration AGT-4 forbids.
        sa.Column("cited_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        # The order's status immediately before the override, so a replay can
        # tell a release-from-held from a release of something already moving.
        sa.Column("order_status_before", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "action IN ('release', 'hold')", name="ck_dispatcher_overrides_action"
        ),
        # CON-2, at the database. The API refusing a request without a reason is
        # the API's promise; this is the system's.
        sa.CheckConstraint(
            f"reason_code IN ({codes})", name="ck_dispatcher_overrides_reason_code"
        ),
        # A note that is present must say something. An empty string would
        # satisfy NOT NULL while carrying nothing, and reads in an export as
        # though somebody wrote an explanation.
        sa.CheckConstraint(
            "note IS NULL OR length(btrim(note)) > 0",
            name="ck_dispatcher_overrides_note_not_blank",
        ),
        # The label is only a label if both halves are there. Knowing the system's
        # decision means having it; not knowing it means having neither.
        sa.CheckConstraint(
            "(system_decision_known AND system_action IS NOT NULL) OR "
            "(NOT system_decision_known AND system_action IS NULL "
            "AND system_reason IS NULL AND cited_snapshot_id IS NULL)",
            name="ck_dispatcher_overrides_label_is_whole",
        ),
    )
    op.create_index("ix_dispatcher_overrides_hub_id", "dispatcher_overrides", ["hub_id"])
    op.create_index("ix_dispatcher_overrides_order_id", "dispatcher_overrides", ["order_id"])
    op.create_index(
        "ix_dispatcher_overrides_overridden_at", "dispatcher_overrides", ["overridden_at"]
    )
    op.create_index(
        "ix_dispatcher_overrides_reason_code", "dispatcher_overrides", ["reason_code"]
    )
    # The export for M2 reads the labelled ones. Partial, because the unlabelled
    # rows are deliberately excluded from it and there is no point indexing them.
    op.create_index(
        "ix_dispatcher_overrides_labelled",
        "dispatcher_overrides",
        ["hub_id", "overridden_at"],
        postgresql_where=sa.text("system_decision_known"),
    )

    op.execute(_IMMUTABLE_FN)
    op.execute(
        """
        CREATE TRIGGER dispatcher_overrides_no_update_or_delete
        BEFORE UPDATE OR DELETE ON dispatcher_overrides
        FOR EACH ROW EXECUTE FUNCTION dispatcher_overrides_are_immutable();
        """
    )


def downgrade() -> None:
    """Drops every override ever recorded.

    Not recoverable from anywhere else. The orders keep their final statuses, so
    the *effect* of each override survives, but who chose it and why does not -
    and the labelled disagreements are the only record of a human and the queue
    reaching different conclusions on the same order. `M2`'s Phase 3 training
    set starts again from zero.
    """
    op.execute(
        "DROP TRIGGER IF EXISTS dispatcher_overrides_no_update_or_delete "
        "ON dispatcher_overrides"
    )
    op.execute("DROP FUNCTION IF EXISTS dispatcher_overrides_are_immutable()")
    for index in (
        "ix_dispatcher_overrides_labelled",
        "ix_dispatcher_overrides_reason_code",
        "ix_dispatcher_overrides_overridden_at",
        "ix_dispatcher_overrides_order_id",
        "ix_dispatcher_overrides_hub_id",
    ):
        op.drop_index(index, table_name="dispatcher_overrides")
    op.drop_table("dispatcher_overrides")
