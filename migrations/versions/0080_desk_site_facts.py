"""Desk site facts: where each site fact came from, and desk-graded records (DE-15)

`docs/ROADMAP_1.5.md` DE-15. The desk pre-fill (`app/record/desk_prefill.py`,
`scripts/desk_prefill.py`) fills a site's kind, setting and shared-address
answer from public data matched at the desk. Two changes here.

**Each site fact keeps its own source.** `locations.site_source` is one value
for the whole site, and the Experiment 0 import already set it to `import` on
every New Jersey site - so a setting the desk adds later could not say it came
from the desk without erasing that the kind came from the import.
`locations.site_fact_sources` is a JSON object, fact to source
(`{"setting": "desk", "kind_of_site": "import"}`); a fact with no entry has the
site's `site_source`. Only the desk pre-fill writes it today.

**A record that counts only because of a desk fact is bronze at most.** The
roadmap's own limit for DE-15: public data is a guess about a place nobody from
LMX has stood at, and a guess must not grade a record as if it were observed.
An end (pickup or drop-off) whose site counts as known only thanks to a fact the
desk supplied marks the record `desk_site`, and a record that would otherwise be
gold or silver is graded bronze. A record already bronze or reference is
unchanged, and so is every record with no desk fact on a counted end - which
today is every record, since nothing has written a desk fact yet. The cap lifts
when a person confirms the fact and its source changes.

Revision ID: 0080
Revises: 0079
"""
import importlib.util
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0080"
down_revision: Union[str, None] = "0079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _view_from(filename: str) -> str:
    """A migration's `_record_tiers()` or `_RECORD_TIERS`, read from its file rather than copied here."""
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "_record_tiers"):
        return module._record_tiers()
    return module._RECORD_TIERS


# (before, after) pairs applied to 0079's view, each asserted present.
_EDITS = (
    # The place facts are known, and at least one of them is the desk's.
    (
        """        ) AS place,
        (
            rp.receiving_hours IS NOT NULL""",
        """        ) AS place,
        COALESCE(l.site_fact_sources->>'kind_of_site' = 'desk', false)
            OR COALESCE(l.site_fact_sources->>'setting' = 'desk', false)
            OR COALESCE(l.site_fact_sources->>'shares_address' = 'desk', false) AS desk_place,
        (
            rp.receiving_hours IS NOT NULL""",
    ),
    (
        """        (h.captured AND s.place) AS captured_for_visit
    FROM handoff_captured h""",
        """        (h.captured AND s.place) AS captured_for_visit,
        s.desk_place
    FROM handoff_captured h""",
    ),
    # An end that counts, and counts on a desk fact.
    (
        """        ) END AS has_pickup,""",
        """        ) END AS has_pickup,
        COALESCE(
            (
                SELECT bool_or(pickup_end.captured AND pickup_end.desk_place)
                FROM stop_orders so
                JOIN stops ps ON ps.id = so.stop_id AND ps.stop_type = 'pickup'
                JOIN end_captured pickup_end ON pickup_end.handoff_point_id = ps.handoff_point_id
                WHERE so.order_id = r.order_id
            ),
            false
        )
        OR COALESCE(
            CASE WHEN r.stop_type = 'visit' THEN dropoff_end.captured_for_visit ELSE dropoff_end.captured END
            AND dropoff_end.desk_place,
            false
        ) AS desk_site,""",
    ),
    (
        """    c.elements_missing,
    CASE""",
        """    c.elements_missing,
    c.desk_site,
    CASE
        -- DE-15: an end known only from the desk caps the record at bronze.
        WHEN c.desk_site AND (
            (c.stop_type = 'visit' AND c.has_dropoff)
            OR (c.stop_type <> 'visit' AND c.elements_missing <= 1)
        ) THEN 'bronze'""",
    ),
)


def _record_tiers() -> str:
    view = _view_from("0079_off_platform_records.py")
    for before, after in _EDITS:
        assert view.count(before) == 1, f"0079's view changed shape near: {before.strip()[:60]!r}; update 0080"
        view = view.replace(before, after)
    return view


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column("site_fact_sources", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.execute("DROP VIEW IF EXISTS record_tiers")
    op.execute(_record_tiers())


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS record_tiers")
    op.execute(_view_from("0079_off_platform_records.py"))
    op.drop_column("locations", "site_fact_sources")
