"""Machine limits v2: the drone and robot from what their operators publish

`docs/ROADMAP_1.5.md` DE-2. `v1` was assumed throughout, from an internal doc and
the order-weight analysis. `v2` takes the drone and the sidewalk robot from what
the operators themselves publish, each figure cited, and keeps the car as it was.
Read 30 October 2026; every page named here was opened.

**Still `assumed`, because no operator has told LMX anything.** `operator_reported`
means an operator confirmed a figure to us, and published spec sheets are not
that: the same operator states different payloads on different pages. What
changes is that each figure now has a public source instead of none.

- **Drone: 2.3 kg**, the payload Zipline flies today rather than the one on its
  fact sheet. Zipline started home delivery in Round Rock on 9 October 2026
  within 3 miles of 3000 E. Palm Valley Blvd, the zone `ml/analytics/
  zones_austin.geojson` already draws. Its fact sheet says 8 lb (3.6 kg) for
  Platform 2, but in service each order is about 5 lb, with 8 lb planned by the
  end of 2026; Amazon's MK30 is also 5 lb. The order is lowered on a tether to an
  approved spot about two parking spaces across, so open ground near the door is
  still needed. Size stays a shoebox: no drone operator publishes dimensions,
  and Amazon's "fits in a large shoebox" is the only stated one. No hazardous
  material: 14 CFR 107.36 forbids it on a Part 107 drone, and under Part 135 it
  needs an authorised hazmat programme nobody has.
- **Sidewalk robot: 9.07 kg** (Starship's "10kgs/20lbs": 20 lb is 9.07 kg, the
  stricter reading). Size stays medium ("3 shopping bags"). It climbs curbs but
  no maximum height is published, so a step-free path and a sidewalk all the way
  stay required. Texas lets these devices run on sidewalks (Transportation Code
  ch. 552A) and bars placarded hazardous material. Starship does not serve
  central Texas.
- **Car: unchanged from `v1`.** The car is a road autonomous vehicle whose
  recipient's staff collect the order from it, so the limits are what one person
  carries (25 kg) and how far (50 m), not the vehicle's payload. What the
  operators publish bears that out: Nuro, the one road AV with published specs,
  carries about 226 kg and only reaches the curb, where the recipient enters a
  code. None delivers in central Texas, and none publishes what it refuses, so
  "no hazardous or liquid" stays the strict default. That default now decides
  many car verdicts, because a drawn order often holds a sealed fluid.

`v1` is left as it was: labels scored under it keep meaning what they meant.

Revision ID: 0085
Revises: 0084
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0085"
down_revision: Union[str, None] = "0084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_READ = "Read 30 Oct 2026. Published by the operator, not confirmed to LMX."

_V2 = [
    {
        "machine": "drone",
        "max_weight_kg": 2.3,
        "max_size_class": "small",
        "hazmat_or_liquid_allowed": False,
        "needs_step_free_path": False,
        "needs_continuous_sidewalk": False,
        "needs_open_ground": True,
        "max_walk_distance_m": None,
        "source": (
            "Zipline Platform 2, live in Round Rock since 9 Oct 2026 "
            "(https://www.zipline.com/newsroom/now-loading-zipline-brings-fast-autonomous-delivery-to-austin). "
            "Weight: about 5 lb (2.3 kg) per order in service "
            "(https://dronexl.co/2026/10/09/zipline-austin-round-rock-drone-delivery-launch/; "
            "https://communityimpact.com/dallas-fort-worth/denton/business/2026/03/24/zipline-drone-delivery-partners-with-walmart-in-denton); "
            "the fact sheet says 8 lb (https://www.zipline.com/about/zipline-fact-sheet), planned by end of 2026. "
            "Amazon MK30 is also 5 lb max (FAA FONSI, https://www.faa.gov/uas/advanced_operations/nepa_and_drones/FONSI_ROD_Amazon_Multi_Metro_TX_Signed.pdf). "
            "Size: no operator publishes dimensions; Amazon's 'fits in a large shoebox' "
            "(https://www.aboutamazon.com/news/transportation/amazon-prime-air-drone-delivery-expansion). "
            "Open ground: tethered to an approved spot, about two parking spaces (fact sheet). "
            "Hazardous material: none, 14 CFR 107.36. " + _READ
        ),
    },
    {
        "machine": "sidewalk_robot",
        "max_weight_kg": 9.07,
        "max_size_class": "medium",
        "hazmat_or_liquid_allowed": False,
        "needs_step_free_path": True,
        "needs_continuous_sidewalk": True,
        "needs_open_ground": False,
        "max_walk_distance_m": None,
        "source": (
            "Starship: 'carries up to 10kgs/20lbs, or 3 shopping bags' (https://sustainable.fostercity.org/?p=402); "
            "20 lb (9.07 kg) taken as the stricter. Climbs curbs, no maximum height published "
            "(https://www.starship.xyz/faq/), so step-free and continuous sidewalk stay required. "
            "Texas allows these devices on sidewalks and bars placarded hazardous material "
            "(Transportation Code ch. 552A, https://texas.public.law/statutes/tex._transp._code_section_552a.0009). "
            "No robot operator serves central Texas. " + _READ
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
            "Unchanged from v1 and still assumed: the recipient's staff collect from the vehicle, so the limit is "
            "a one-person carry (25 kg) within 50 m of the door. Nuro, the one road AV with published specs, "
            "carries about 226 kg and only reaches the curb, where the recipient enters a code "
            "(https://insideevs.com/news/560458/nuro-autonomous-delivery-vehicle-byd/); none delivers in central "
            "Texas. Hazardous or liquid: none until decided, as no operator publishes what it refuses; a "
            "lead-acid battery by road is regulated under 49 CFR 173.159."
        ),
    },
]


def upgrade() -> None:
    limits = sa.table(
        "machine_limits",
        sa.column("version", sa.String),
        sa.column("machine", sa.String),
        sa.column("max_weight_kg", sa.Numeric),
        sa.column("max_size_class", sa.String),
        sa.column("hazmat_or_liquid_allowed", sa.Boolean),
        sa.column("needs_step_free_path", sa.Boolean),
        sa.column("needs_continuous_sidewalk", sa.Boolean),
        sa.column("needs_open_ground", sa.Boolean),
        sa.column("max_walk_distance_m", sa.Float),
        sa.column("basis", sa.String),
        sa.column("source", sa.Text),
        sa.column("recorded_by", sa.String),
    )
    op.bulk_insert(
        limits,
        [{**row, "version": "v2", "basis": "assumed", "recorded_by": "DE-2 published specs"} for row in _V2],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM machine_limits WHERE version = 'v2'"))
