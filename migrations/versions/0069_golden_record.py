"""The golden record's four layers, and the tier every record is graded by.

`docs/ROADMAP_1.5.md` DE-1, decisions D-GR and D-TIER. The format is the one
the definitions doc ("LMX data engine: What and How?") locks: four linked layers
- Site, Handoff point, Delivery, Labels - reusing what the tree already has
wherever it fits.

  - **Site** is the identity layer's `locations` row. It gains kind of site,
    setting, region, whether it shares its address (and, in
    `site_address_shares`, with whom) and where those facts came from.
    Receiving hours and who receives already live on `receiver_profiles`
    (`set_receiving_hours`, `who_receives`) and are not duplicated.
  - **Handoff point** is new: `handoff_points`, one row per door, bay or counter
    a delivery can use - the row the modality model learns from.
  - **Delivery** is a drop-off or visit `stop`: it gains the handoff point it
    used and the record's source and collector. The item's real weight, size and
    hazardous/liquid flag go on `orders`, beside dispatch's `weight_units`. The
    delivery profile - urgency, time window, distance - is already there
    (`sla_tier`, `promised_at` / `delivery_window_end`, the two ends'
    coordinates), so nothing is added for it.
  - **Labels** are new: `machine_labels`, a verdict per machine per record.

`record_tiers` is a view, not a table: a tier is a fact about how complete a
record is right now, so it is computed from the record rather than stored where
it could go stale. One row per record - a drop-off or visit stop - with the five
elements, how many are missing, and the tier:

    gold       all five captured
    silver     one missing
    bronze     two missing
    reference  three or more missing

An element counts only when every field the definitions mark "needed for gold"
is non-null; unknown is null, never a guess. The two list fields must be JSON
arrays: an empty list is "checked, nothing there", a JSON null is unknown. A
handoff point on a merged location reads the site facts of the location it was
merged into.

    pickup / drop-off   that end's handoff point captured, and its site
                        (kind, setting, shared-address answer, receiving hours,
                        who receives)
    item                description, weight, size class, hazardous/liquid flag
    delivery profile    urgency, a time window, and both ends located
    autonomy qualifier  a first scorer's verdict for all three machines

Revision ID: 0069
Revises: 0068
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069"
down_revision: Union[str, None] = "0068"
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
    SELECT
        c.location_id AS id,
        (
            l.kind_of_site IS NOT NULL
            AND l.setting IS NOT NULL
            AND l.shares_address IS NOT NULL
            AND rp.receiving_hours IS NOT NULL
            AND rp.who_receives IS NOT NULL
        ) AS captured
    FROM canonical c
    JOIN locations l ON l.id = c.canonical_id
    LEFT JOIN receiver_profiles rp ON rp.location_id = c.canonical_id
),
end_captured AS (
    SELECT h.id AS handoff_point_id, h.location_id, (h.captured AND s.captured) AS captured
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
        COALESCE(
            (
                SELECT bool_or(pickup_end.captured)
                FROM stop_orders so
                JOIN stops ps ON ps.id = so.stop_id AND ps.stop_type = 'pickup'
                JOIN end_captured pickup_end ON pickup_end.handoff_point_id = ps.handoff_point_id
                WHERE so.order_id = r.order_id
            ),
            false
        ) AS has_pickup,
        COALESCE(dropoff_end.captured, false) AS has_dropoff,
        COALESCE(
            o.item_description IS NOT NULL
            AND o.item_weight_kg IS NOT NULL
            AND o.item_size_class IS NOT NULL
            AND o.item_hazmat_or_liquid IS NOT NULL,
            false
        ) AS has_item,
        COALESCE(
            o.sla_tier IS NOT NULL
            AND COALESCE(o.promised_at, o.delivery_window_end) IS NOT NULL
            AND o.delivery_lat IS NOT NULL AND o.delivery_lng IS NOT NULL
            AND sh.lat IS NOT NULL AND sh.lng IS NOT NULL,
            false
        ) AS has_delivery_profile,
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
    SELECT
        e.*,
        (NOT e.has_pickup)::int
        + (NOT e.has_dropoff)::int
        + (NOT e.has_item)::int
        + (NOT e.has_delivery_profile)::int
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
    CASE c.elements_missing
        WHEN 0 THEN 'gold'
        WHEN 1 THEN 'silver'
        WHEN 2 THEN 'bronze'
        ELSE 'reference'
    END AS tier
FROM counted c
"""


def upgrade() -> None:
    # --- Site ----------------------------------------------------------------
    op.add_column("locations", sa.Column("kind_of_site", sa.String(32), nullable=True))
    op.add_column("locations", sa.Column("setting", sa.String(12), nullable=True))
    op.add_column("locations", sa.Column("region", sa.String(16), nullable=True))
    op.add_column("locations", sa.Column("shares_address", sa.Boolean(), nullable=True))
    op.add_column("locations", sa.Column("site_source", sa.String(24), nullable=True))
    op.create_check_constraint(
        "ck_locations_setting", "locations", "setting IS NULL OR setting IN ('city_core', 'suburb')"
    )
    op.create_table(
        "site_address_shares",
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), primary_key=True),
        sa.Column(
            "shares_with_location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("location_id <> shares_with_location_id", name="ck_site_address_shares_not_self"),
    )

    # --- Handoff point -------------------------------------------------------
    op.create_table(
        "handoff_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("handoff_type", sa.String(24), nullable=True),
        sa.Column("door_lat", sa.Float(), nullable=True),
        sa.Column("door_lng", sa.Float(), nullable=True),
        sa.Column("stop_lat", sa.Float(), nullable=True),
        sa.Column("stop_lng", sa.Float(), nullable=True),
        sa.Column("stop_type", sa.String(16), nullable=True),
        sa.Column("stop_legal", sa.Boolean(), nullable=True),
        sa.Column("walk_distance_m", sa.Float(), nullable=True),
        sa.Column("steps_or_ramps", sa.String(16), nullable=True),
        sa.Column("continuous_sidewalk", sa.Boolean(), nullable=True),
        sa.Column("obstructions", postgresql.JSONB(), nullable=True),
        sa.Column("drone_open_ground", sa.Boolean(), nullable=True),
        sa.Column("drone_ground_distance_m", sa.Float(), nullable=True),
        sa.Column("overhead", postgresql.JSONB(), nullable=True),
        sa.Column("door_width_cm", sa.Integer(), nullable=True),
        sa.Column("dock_height_cm", sa.Integer(), nullable=True),
        sa.Column("photo_approach", sa.String(500), nullable=True),
        sa.Column("photo_stop_point", sa.String(500), nullable=True),
        sa.Column("photo_path", sa.String(500), nullable=True),
        sa.Column("photo_handoff", sa.String(500), nullable=True),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("collected_by", sa.String(64), nullable=True),
        sa.Column("collected_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "handoff_type IS NULL OR handoff_type IN "
            "('counter', 'roll_up_bay', 'loading_dock', 'side_door', 'job_site_trailer', 'mailroom')",
            name="ck_handoff_points_type",
        ),
        sa.CheckConstraint(
            "stop_type IS NULL OR stop_type IN ('curb', 'lot', 'alley', 'loading_zone', 'dock_apron')",
            name="ck_handoff_points_stop_type",
        ),
        sa.CheckConstraint(
            "steps_or_ramps IS NULL OR steps_or_ramps IN ('none', 'steps', 'ramp', 'steps_and_ramp')",
            name="ck_handoff_points_steps_or_ramps",
        ),
        sa.CheckConstraint(
            "walk_distance_m IS NULL OR walk_distance_m >= 0", name="ck_handoff_points_walk_distance"
        ),
    )
    op.create_index("ix_handoff_points_location_id", "handoff_points", ["location_id"])

    # --- Delivery ------------------------------------------------------------
    op.add_column(
        "stops",
        sa.Column(
            "handoff_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("handoff_points.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_stops_handoff_point_id", "stops", ["handoff_point_id"])
    op.add_column(
        "stops",
        sa.Column("record_source", sa.String(24), nullable=False, server_default="lmx_app"),
    )
    op.add_column("stops", sa.Column("collected_by", sa.String(64), nullable=True))
    op.add_column("orders", sa.Column("item_description", sa.String(255), nullable=True))
    op.add_column("orders", sa.Column("item_weight_kg", sa.Numeric(8, 2), nullable=True))
    op.add_column("orders", sa.Column("item_size_class", sa.String(16), nullable=True))
    op.add_column("orders", sa.Column("item_hazmat_or_liquid", sa.Boolean(), nullable=True))
    op.create_check_constraint(
        "ck_orders_item_weight_kg", "orders", "item_weight_kg IS NULL OR item_weight_kg >= 0"
    )
    # The intake schema's size buckets (`SizeClass` in app/schemas/lmx_order.py),
    # which a counter person picks without a tape measure. Never stored until now.
    op.create_check_constraint(
        "ck_orders_item_size_class",
        "orders",
        "item_size_class IS NULL OR item_size_class IN ('envelope', 'small', 'medium', 'large', 'oversize')",
    )

    # --- Labels --------------------------------------------------------------
    op.create_table(
        "machine_labels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("stop_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stops.id"), nullable=False),
        sa.Column(
            "handoff_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("handoff_points.id"),
            nullable=True,
        ),
        sa.Column("machine", sa.String(16), nullable=False),
        sa.Column("verdict", sa.String(12), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("scorer_id", sa.String(64), nullable=False),
        sa.Column("is_blind_second", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("machine_limits_version", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("machine IN ('drone', 'sidewalk_robot', 'car')", name="ck_machine_labels_machine"),
        sa.CheckConstraint("verdict IN ('yes', 'no', 'cant_tell')", name="ck_machine_labels_verdict"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_machine_labels_confidence"
        ),
    )
    op.create_index("ix_machine_labels_stop_id", "machine_labels", ["stop_id"])

    op.execute(_RECORD_TIERS)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS record_tiers")
    op.drop_index("ix_machine_labels_stop_id", table_name="machine_labels")
    op.drop_table("machine_labels")
    op.drop_constraint("ck_orders_item_size_class", "orders", type_="check")
    op.drop_constraint("ck_orders_item_weight_kg", "orders", type_="check")
    op.drop_column("orders", "item_hazmat_or_liquid")
    op.drop_column("orders", "item_size_class")
    op.drop_column("orders", "item_weight_kg")
    op.drop_column("orders", "item_description")
    op.drop_column("stops", "collected_by")
    op.drop_column("stops", "record_source")
    op.drop_index("ix_stops_handoff_point_id", table_name="stops")
    op.drop_column("stops", "handoff_point_id")
    op.drop_index("ix_handoff_points_location_id", table_name="handoff_points")
    op.drop_table("handoff_points")
    op.drop_table("site_address_shares")
    op.drop_constraint("ck_locations_setting", "locations", type_="check")
    op.drop_column("locations", "site_source")
    op.drop_column("locations", "shares_address")
    op.drop_column("locations", "region")
    op.drop_column("locations", "setting")
    op.drop_column("locations", "kind_of_site")
