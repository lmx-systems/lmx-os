"""Machine limits, versioned, and a label that cannot save without them

`docs/ROADMAP_1.5.md` DE-2. `machine_limits` holds what each machine - drone,
sidewalk robot, car - can carry and where it can go, three rows per version
(`app/models/machine_limit.py` says what each limit means). Versions are added,
never edited.

`machine_labels.machine_limits_version` becomes required, with a foreign key on
(version, machine): a verdict names the limits it was scored against, and those
limits must have a row for the verdict's machine. Nothing writes labels yet
(`DE-9` is the writer), so no row can be missing a version; if one is, the
upgrade stops rather than guess which limits it was scored under.

**The seed version, `v1`, is assumed throughout.** Its figures come from the July
2026 autonomy integration doc and the order-weight analysis, and the two disagree
on the drone's payload (2.5 kg against about 9 lb). `v1` takes 2.5 kg. Where a
limit is unknown it is set strict rather than loose, because a wrong "yes" sends
a machine that fails and a wrong "no" only leaves an order with a driver. Rich is
asking the operators for the real figures; they arrive as a new version through
`scripts/machine_limits.py`.

Revision ID: 0073
Revises: 0072
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: Union[str, None] = "0072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NO_HAZMAT = (
    " Hazardous or liquid items: none, because no operator has said what it accepts and a "
    "wrong yes costs more than a wrong no."
)

_V1 = [
    {
        "machine": "drone",
        "max_weight_kg": 2.5,
        "max_size_class": "small",
        "hazmat_or_liquid_allowed": False,
        "needs_step_free_path": False,
        "needs_continuous_sidewalk": False,
        "needs_open_ground": True,
        "max_walk_distance_m": None,
        "source": (
            "Assumed. Weight: 2.5 kg, the payload the order-weight analysis used; the July 2026 "
            "autonomy integration doc says about 9 lb (4.1 kg), so this is open until the drone "
            "operator confirms. Size: fits a shoebox. Needs a clear landing spot near the door."
            + _NO_HAZMAT
        ),
    },
    {
        "machine": "sidewalk_robot",
        "max_weight_kg": 9.0,
        "max_size_class": "medium",
        "hazmat_or_liquid_allowed": False,
        "needs_step_free_path": True,
        "needs_continuous_sidewalk": True,
        "needs_open_ground": False,
        "max_walk_distance_m": None,
        "source": (
            "Assumed. Weight: about 20 lb (9 kg), per the July 2026 autonomy integration doc, "
            "which says internal volume binds first. Size: fits a carry-on bag. Needs a "
            "sidewalk-reachable entrance with no steps." + _NO_HAZMAT
        ),
    },
    {
        "machine": "car",
        "max_weight_kg": 25.0,
        "max_size_class": "large",
        "hazmat_or_liquid_allowed": False,
        "needs_step_free_path": False,
        "needs_continuous_sidewalk": False,
        "needs_open_ground": False,
        "max_walk_distance_m": 50.0,
        "source": (
            "Assumed. Weight: the July 2026 autonomy integration doc says 'tens of lb'; 25 kg is "
            "the taxonomy's one-person carry band edge. Size: fits a car boot. Staff collect from "
            "the vehicle, so the stop must be within 50 m of the door (assumed)." + _NO_HAZMAT
        ),
    },
]


def upgrade() -> None:
    limits = op.create_table(
        "machine_limits",
        sa.Column("version", sa.String(32), primary_key=True),
        sa.Column("machine", sa.String(16), primary_key=True),
        sa.Column("max_weight_kg", sa.Numeric(8, 2), nullable=False),
        sa.Column("max_size_class", sa.String(16), nullable=False),
        sa.Column("hazmat_or_liquid_allowed", sa.Boolean(), nullable=False),
        sa.Column("needs_step_free_path", sa.Boolean(), nullable=False),
        sa.Column("needs_continuous_sidewalk", sa.Boolean(), nullable=False),
        sa.Column("needs_open_ground", sa.Boolean(), nullable=False),
        sa.Column("max_walk_distance_m", sa.Float(), nullable=True),
        sa.Column("basis", sa.String(24), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("machine IN ('drone', 'sidewalk_robot', 'car')", name="ck_machine_limits_machine"),
        sa.CheckConstraint("max_weight_kg > 0", name="ck_machine_limits_weight"),
        sa.CheckConstraint(
            "max_size_class IN ('envelope', 'small', 'medium', 'large', 'oversize')",
            name="ck_machine_limits_size_class",
        ),
        sa.CheckConstraint(
            "max_walk_distance_m IS NULL OR max_walk_distance_m >= 0", name="ck_machine_limits_walk_distance"
        ),
        sa.CheckConstraint(
            "basis IN ('assumed', 'operator_reported', 'measured')", name="ck_machine_limits_basis"
        ),
    )
    op.bulk_insert(
        limits,
        [{**row, "version": "v1", "basis": "assumed", "recorded_by": "DE-2 seed"} for row in _V1],
    )

    unversioned = op.get_bind().execute(
        sa.text("SELECT count(*) FROM machine_labels WHERE machine_limits_version IS NULL")
    ).scalar_one()
    if unversioned:
        raise RuntimeError(
            f"{unversioned} machine label(s) have no limits version. Say which version each was "
            "scored under before making the column required - do not default them to v1."
        )
    op.alter_column("machine_labels", "machine_limits_version", existing_type=sa.String(32), nullable=False)
    op.create_foreign_key(
        "fk_machine_labels_limits",
        "machine_labels",
        "machine_limits",
        ["machine_limits_version", "machine"],
        ["version", "machine"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_machine_labels_limits", "machine_labels", type_="foreignkey")
    op.alter_column("machine_labels", "machine_limits_version", existing_type=sa.String(32), nullable=True)
    op.drop_table("machine_limits")
