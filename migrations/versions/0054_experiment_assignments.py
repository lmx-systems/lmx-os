"""experiment_assignments: which arm an order was in, and on what terms

EXP-1 (docs/ROADMAP_1.5.md Phase 2). Done when "arm assigned at intake,
immutable, in the contract before the code". All three clauses are enforced
here rather than described.

**Immutable** - a trigger rejects UPDATE and DELETE, the same shape 0051 uses.
An arm that can be re-rolled is not an arm. The failure this guards against is
not fraud: it is a well-meaning engineer moving one order out of the control
group because the customer complained, which is precisely the order whose
presence in that group the measurement depends on.

**In the contract before the code** - `clients.control_arm_contracted_at` is
the gate, and it is a date rather than a boolean on purpose. Turning the arm on
requires stating when the customer agreed, which is a thing you either have or
do not, and it is copied onto every assignment so the answer to "was I in an
experiment, and did I agree" comes from the data rather than from memory.
`MODEL_AND_DATA_BRIEF.md`: *"an arm discovered rather than disclosed looks like
negligence."*

**Off by default.** Both new client columns are nullable with no default, so
every existing client is out of the experiment until somebody deliberately
enrols them. A migration that switched the arm on for the current book would be
the disclosure failure the brief warns about, performed by a deploy.

`draw` and `salt` are stored so the assignment can be recomputed instead of
trusted - that is what lets EXP-3 verify an arm, and what makes a re-roll
visible, since the recomputed value would stop matching.

Revision ID: 0054
Revises: 0053
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION experiment_assignments_are_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'experiment_assignments is append-only: % on row % was rejected. '
        'An arm that can be re-rolled is not an arm.',
        TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "experiment_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hub_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experiment", sa.String(length=32), nullable=False),
        sa.Column("arm", sa.String(length=16), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("control_fraction", sa.Float(), nullable=False),
        sa.Column("draw", sa.Float(), nullable=False),
        sa.Column("contracted_at", sa.DateTime(timezone=True), nullable=False),
        # One arm per order, at the database. Two assignments would be two
        # answers to which arm an order was in, and nothing could choose.
        sa.UniqueConstraint("order_id", name="uq_experiment_assignments_order"),
        sa.CheckConstraint("arm IN ('control', 'treatment')", name="ck_experiment_assignments_arm"),
        sa.CheckConstraint("draw >= 0 AND draw < 1", name="ck_experiment_assignments_draw"),
        # The band the roadmap specifies. Below 5% the control group says
        # nothing within a quarter; above 10% we are giving a paying customer a
        # worse service on more orders than the measurement needs.
        sa.CheckConstraint(
            "control_fraction >= 0.05 AND control_fraction <= 0.10",
            name="ck_experiment_assignments_fraction",
        ),
    )
    op.create_index("ix_experiment_assignments_hub_id", "experiment_assignments", ["hub_id"])
    op.create_index("ix_experiment_assignments_client_id", "experiment_assignments", ["client_id"])
    op.create_index("ix_experiment_assignments_arm", "experiment_assignments", ["arm"])
    op.create_index(
        "ix_experiment_assignments_assigned_at", "experiment_assignments", ["assigned_at"]
    )
    op.create_index(
        "ix_experiment_assignments_experiment", "experiment_assignments", ["experiment"]
    )

    op.execute(_IMMUTABLE_FN)
    op.execute(
        """
        CREATE TRIGGER experiment_assignments_no_update_or_delete
        BEFORE UPDATE OR DELETE ON experiment_assignments
        FOR EACH ROW EXECUTE FUNCTION experiment_assignments_are_immutable();
        """
    )

    # The gate. Nullable, no default: every existing client is out until
    # somebody enrols them deliberately.
    op.add_column(
        "clients",
        sa.Column("control_arm_contracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("clients", sa.Column("control_arm_fraction", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_clients_control_arm_fraction",
        "clients",
        "control_arm_fraction IS NULL OR "
        "(control_arm_fraction >= 0.05 AND control_arm_fraction <= 0.10)",
    )
    # A fraction without a date is an arm nobody agreed to. Stated at the
    # database so it cannot be half-configured by a script that set one field.
    op.create_check_constraint(
        "ck_clients_control_arm_contracted",
        "clients",
        "control_arm_fraction IS NULL OR control_arm_contracted_at IS NOT NULL",
    )


def downgrade() -> None:
    """Drops the assignments.

    Not recoverable in any meaningful sense: the assignments can be recomputed
    from the salt and the fraction, but only for orders that still exist, and
    the record of which customers had agreed to an arm at the time goes with
    the client columns. Any savings statement resting on a control comparison
    made before this loses its basis.
    """
    op.drop_constraint("ck_clients_control_arm_contracted", "clients", type_="check")
    op.drop_constraint("ck_clients_control_arm_fraction", "clients", type_="check")
    op.drop_column("clients", "control_arm_fraction")
    op.drop_column("clients", "control_arm_contracted_at")

    op.execute(
        "DROP TRIGGER IF EXISTS experiment_assignments_no_update_or_delete "
        "ON experiment_assignments"
    )
    op.execute("DROP FUNCTION IF EXISTS experiment_assignments_are_immutable()")
    for index in (
        "ix_experiment_assignments_experiment",
        "ix_experiment_assignments_assigned_at",
        "ix_experiment_assignments_arm",
        "ix_experiment_assignments_client_id",
        "ix_experiment_assignments_hub_id",
    ):
        op.drop_index(index, table_name="experiment_assignments")
    op.drop_table("experiment_assignments")
