"""Technician visits: a `visit` stop, and how a visit is graded (D-VISIT)

`docs/ROADMAP_1.5.md` DE-6 and §2.5 D-VISIT (Sourabh, 12 Oct 2026). A visit is a
stop with no delivery - a technician at a site on a re-drive, capturing its door.
It has no order, so under D-TIER's five elements its pickup, item and delivery
profile were all "missing" and every visit graded reference, however completely
the door was captured. D-VISIT grades a visit on the two elements it can have:

    gold       drop-off end and autonomy qualifier
    silver     drop-off end; the qualifier comes later, from the labelling queue
    reference  no drop-off end - a visit that did not capture its door holds nothing

A visit's pickup, item and delivery profile are not applicable, so the view
reports them as null rather than false, and they are not counted in
`elements_missing`. Bronze is not reachable for a visit.

**A visit's drop-off end does not need the access facts.** For a delivery, the
drop-off end is the handoff point captured plus the site: kind, setting, whether
it shares its address, receiving hours and who receives. A technician who hands
nothing over cannot observe the last two - DE-6 records access as unknown at a
visit - so for a visit the end is the handoff point plus the site's kind,
setting and shared-address answer. Receiving hours and who receives stay where
they are, filled by a delivery or the desk, and a delivery is graded exactly as
before.

`stops.stop_type` gains a check: pickup, dropoff or visit. Nothing wrote any
other value; a typo'd type would otherwise be a stop every reader skips.

Revision ID: 0077
Revises: 0076
"""
import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op

revision: str = "0077"
down_revision: Union[str, None] = "0076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RECORD_TIERS = """
CREATE VIEW record_tiers AS
WITH RECURSIVE merge_chain AS (
    -- A merged location's site facts are its survivor's: follow
    -- merged_into_id to the end, capped at 16 hops as identity resolution is.
    SELECT l.id AS location_id, l.id AS at_id, l.merged_into_id, 0 AS depth
    FROM locations l
    UNION ALL
    SELECT c.location_id, l.id, l.merged_into_id, c.depth + 1
    FROM merge_chain c
    JOIN locations l ON l.id = c.merged_into_id
    WHERE c.depth < 16
),
canonical AS (
    SELECT location_id, at_id AS canonical_id FROM merge_chain WHERE merged_into_id IS NULL
),
handoff_captured AS (
    SELECT
        hp.id,
        hp.location_id,
        (
            hp.handoff_type IS NOT NULL
            AND hp.door_lat IS NOT NULL AND hp.door_lng IS NOT NULL
            AND hp.stop_lat IS NOT NULL AND hp.stop_lng IS NOT NULL
            AND hp.stop_type IS NOT NULL AND hp.stop_legal IS NOT NULL
            AND hp.walk_distance_m IS NOT NULL AND hp.steps_or_ramps IS NOT NULL
            AND hp.continuous_sidewalk IS NOT NULL
            AND jsonb_typeof(hp.obstructions) = 'array'
            AND hp.drone_open_ground IS NOT NULL
            AND jsonb_typeof(hp.overhead) = 'array'
            AND hp.photo_approach IS NOT NULL AND hp.photo_stop_point IS NOT NULL
            AND hp.photo_path IS NOT NULL AND hp.photo_handoff IS NOT NULL
        ) AS captured
    FROM handoff_points hp
),
site_captured AS (
    -- `place`: what anyone standing outside can see. `access`: what only
    -- somebody handing something over learns. A delivery needs both; a visit
    -- records access as unknown (D-VISIT).
    SELECT
        c.location_id AS id,
        (
            l.kind_of_site IS NOT NULL
            AND l.setting IS NOT NULL
            AND l.shares_address IS NOT NULL
        ) AS place,
        (
            rp.receiving_hours IS NOT NULL
            AND rp.who_receives IS NOT NULL
        ) AS access
    FROM canonical c
    JOIN locations l ON l.id = c.canonical_id
    LEFT JOIN receiver_profiles rp ON rp.location_id = c.canonical_id
),
end_captured AS (
    SELECT
        h.id AS handoff_point_id,
        h.location_id,
        (h.captured AND s.place AND COALESCE(s.access, false)) AS captured,
        (h.captured AND s.place) AS captured_for_visit
    FROM handoff_captured h
    JOIN site_captured s ON s.id = h.location_id
),
records AS (
    SELECT
        st.id AS stop_id,
        st.stop_type,
        st.handoff_point_id,
        st.record_source,
        st.collected_by,
        COALESCE(st.completed_at, st.arrived_at, st.created_at) AS recorded_at,
        (
            SELECT so.order_id FROM stop_orders so
            WHERE so.stop_id = st.id
            ORDER BY so.created_at, so.order_id
            LIMIT 1
        ) AS order_id
    FROM stops st
    WHERE st.stop_type IN ('dropoff', 'visit')
),
elements AS (
    SELECT
        r.*,
        dropoff_end.location_id AS site_id,
        CASE WHEN r.stop_type = 'visit' THEN NULL ELSE COALESCE(
            (
                SELECT bool_or(pickup_end.captured)
                FROM stop_orders so
                JOIN stops ps ON ps.id = so.stop_id AND ps.stop_type = 'pickup'
                JOIN end_captured pickup_end ON pickup_end.handoff_point_id = ps.handoff_point_id
                WHERE so.order_id = r.order_id
            ),
            false
        ) END AS has_pickup,
        COALESCE(
            CASE WHEN r.stop_type = 'visit' THEN dropoff_end.captured_for_visit ELSE dropoff_end.captured END,
            false
        ) AS has_dropoff,
        CASE WHEN r.stop_type = 'visit' THEN NULL ELSE COALESCE(
            o.item_description IS NOT NULL
            AND o.item_weight_kg IS NOT NULL
            AND o.item_size_class IS NOT NULL
            AND o.item_hazmat_or_liquid IS NOT NULL,
            false
        ) END AS has_item,
        CASE WHEN r.stop_type = 'visit' THEN NULL ELSE COALESCE(
            o.sla_tier IS NOT NULL
            AND COALESCE(o.promised_at, o.delivery_window_end) IS NOT NULL
            AND o.delivery_lat IS NOT NULL AND o.delivery_lng IS NOT NULL
            AND sh.lat IS NOT NULL AND sh.lng IS NOT NULL,
            false
        ) END AS has_delivery_profile,
        EXISTS (
            -- One first scorer covering all three machines; verdicts from
            -- different scorers are not pooled into one qualifier.
            SELECT 1
            FROM machine_labels ml
            WHERE ml.stop_id = r.stop_id
              AND NOT ml.is_blind_second
              AND ml.machine IN ('drone', 'sidewalk_robot', 'car')
            GROUP BY ml.scorer_id
            HAVING count(DISTINCT ml.machine) = 3
        ) AS has_autonomy_qualifier
    FROM records r
    LEFT JOIN end_captured dropoff_end ON dropoff_end.handoff_point_id = r.handoff_point_id
    LEFT JOIN orders o ON o.id = r.order_id
    LEFT JOIN shop_profiles sh ON sh.id = o.shop_id
),
counted AS (
    -- Not applicable is null, and counts as nothing missing.
    SELECT
        e.*,
        COALESCE((NOT e.has_pickup)::int, 0)
        + (NOT e.has_dropoff)::int
        + COALESCE((NOT e.has_item)::int, 0)
        + COALESCE((NOT e.has_delivery_profile)::int, 0)
        + (NOT e.has_autonomy_qualifier)::int AS elements_missing
    FROM elements e
)
SELECT
    c.stop_id,
    c.order_id,
    c.site_id,
    c.handoff_point_id,
    c.stop_type,
    c.record_source,
    c.collected_by,
    c.recorded_at,
    c.has_pickup,
    c.has_dropoff,
    c.has_item,
    c.has_delivery_profile,
    c.has_autonomy_qualifier,
    c.elements_missing,
    CASE
        -- D-VISIT: a visit is its door. Without it there is nothing to grade.
        WHEN c.stop_type = 'visit' AND NOT c.has_dropoff THEN 'reference'
        WHEN c.stop_type = 'visit' AND c.has_autonomy_qualifier THEN 'gold'
        WHEN c.stop_type = 'visit' THEN 'silver'
        WHEN c.elements_missing = 0 THEN 'gold'
        WHEN c.elements_missing = 1 THEN 'silver'
        WHEN c.elements_missing = 2 THEN 'bronze'
        ELSE 'reference'
    END AS tier
FROM counted c
"""


def _previous_view() -> str:
    """0069's view, read from its migration rather than copied here."""
    path = Path(__file__).with_name("0069_golden_record.py")
    spec = importlib.util.spec_from_file_location("golden_record_0069", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._RECORD_TIERS


def upgrade() -> None:
    op.create_check_constraint(
        "ck_stops_stop_type", "stops", "stop_type IN ('pickup', 'dropoff', 'visit')"
    )
    op.execute("DROP VIEW IF EXISTS record_tiers")
    op.execute(_RECORD_TIERS)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS record_tiers")
    op.execute(_previous_view())
    op.drop_constraint("ck_stops_stop_type", "stops", type_="check")
