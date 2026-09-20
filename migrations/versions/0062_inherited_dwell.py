"""receiver_profiles.inherited_dwell_*: what a previous operator measured (`IDN-4`)

`M1`'s problem is cold start. A dock we have never delivered to has no dwell, and
`refresh_dwell_statistics` can only compute from stops we made - of which, for a
new dock, there are none.

The design partner's second-precision export carries real dwell for hundreds of
the same physical docks, measured by whoever was driving before us. That is worth
having and it is **not the same fact** as our own observation, so it gets its own
columns rather than being written into the observed ones.

## Why separate columns and not the same ones

Three reasons, any one of which is sufficient.

**The nightly refresh would erase it.** `refresh_hub_dwell_statistics` recomputes
`dwell_p50_seconds` from our stops every night. An imported figure written there
survives until 2am and is then replaced by a percentile over two deliveries.

**They are different measurements.** Different drivers, different vehicles,
possibly a different process at the same door. Dwell is mostly a property of the
dock - that is `M1`'s whole premise - but "mostly" is a claim to be checked, not
assumed, and it cannot be checked once the two are in one column.

**`MODEL_AND_DATA_BRIEF.md` §418 is explicit**: *"whether the learning transfers
is measured, not assumed."* That line is about `PRD-8`'s cross-customer question
and this is narrower - the same dock under a previous operator - but the
discipline is the same. Two numbers, both visible, and a stated rule for which
one answers.

## What is deliberately not here

No `inherited_dwell_p90`. The import is a prior for a dock we have not visited,
and a tail estimate from somebody else's operation is the number most likely to
be quoted and least likely to survive contact with our own drivers.

Nullable throughout with no default: a dock nobody imported has not inherited
anything, and zero would say it inherited a dwell of nothing.

Revision ID: 0062
Revises: 0061
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "receiver_profiles",
        sa.Column("inherited_dwell_p50_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "receiver_profiles",
        sa.Column("inherited_dwell_sample_count", sa.Integer(), nullable=True),
    )
    # Who measured it. A string rather than a boolean because the next one will
    # not be the same operator, and "inherited" with no idea from whom is a
    # figure nobody can decide whether to trust.
    op.add_column(
        "receiver_profiles",
        sa.Column("inherited_dwell_source", sa.String(length=64), nullable=True),
    )
    # The period the observations came from, not when we imported them. A dwell
    # measured two years ago at a dock that has since rebuilt its receiving bay
    # is worth less than a recent one, and the import date cannot tell you that.
    op.add_column(
        "receiver_profiles",
        sa.Column("inherited_dwell_observed_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "receiver_profiles",
        sa.Column("inherited_dwell_observed_to", sa.DateTime(timezone=True), nullable=True),
    )
    # A figure with no source is not attributable, and an unattributable prior
    # is indistinguishable from an invented one. Enforced here so an import
    # script cannot half-populate it.
    op.create_check_constraint(
        "ck_receiver_profiles_inherited_dwell_attributed",
        "receiver_profiles",
        "inherited_dwell_p50_seconds IS NULL OR ("
        "inherited_dwell_source IS NOT NULL AND inherited_dwell_sample_count IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_receiver_profiles_inherited_dwell_positive",
        "receiver_profiles",
        "inherited_dwell_sample_count IS NULL OR inherited_dwell_sample_count > 0",
    )


def downgrade() -> None:
    """Drops the inherited figures.

    Re-importable from the source export, so this is recoverable - unlike most
    of the downgrades in this tree. The export lives outside the repo
    (`lmx-dwell/` is gitignored), so "recoverable" assumes somebody still has it.
    """
    op.drop_constraint(
        "ck_receiver_profiles_inherited_dwell_positive", "receiver_profiles", type_="check"
    )
    op.drop_constraint(
        "ck_receiver_profiles_inherited_dwell_attributed", "receiver_profiles", type_="check"
    )
    for column in (
        "inherited_dwell_observed_to",
        "inherited_dwell_observed_from",
        "inherited_dwell_source",
        "inherited_dwell_sample_count",
        "inherited_dwell_p50_seconds",
    ):
        op.drop_column("receiver_profiles", column)
