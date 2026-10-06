# Data dictionary

**Draft · 6 October 2026 · unsigned**

`REC-5` in `docs/ROADMAP_1.5.md` asks for one page defining dwell, stop, order,
route and, since v1.4, the golden record, signed by all three founders. This
draft covers only the golden record, written from the schema `DE-1` built
(`migrations/versions/0069_golden_record.py`). Dwell, stop, order and route are
not defined here: three sources currently disagree about them, and settling
that is the founders' call, not the schema's.

Nothing in this page is agreed until it is signed. Sourabh locked the golden
record's format on 6 October (decision D-GR); Matan and Rich have not signed.

---

## The golden record

A **record** is one delivery or visit, from one source, on one date. In the
database it is a drop-off or visit stop. It has four linked layers (D-GR).

| Layer | What it is | Where it lives |
|---|---|---|
| Site | A commercial address: what kind of business, where, who receives and when | `locations`, `receiver_profiles`, `site_address_shares` |
| Handoff point | One door, bay or counter at a site. **The row the modality model learns from** | `handoff_points` |
| Delivery | The drop-off or visit, and the order it carried | `stops`, `orders` |
| Labels | A person's verdict, per machine, on whether that machine could have made this delivery | `machine_labels` |

Unknown is stored as **null, never a guess**. A null field makes its element
missing, and a missing element lowers the tier.

### Site

| Field | Meaning | Values |
|---|---|---|
| `locations.kind_of_site` | What kind of business is at the address | Taxonomy code. Overlaps `node_class` (seven classes); the two are not reconciled yet |
| `locations.setting` | Whether the site is in a city core or a suburb | `city_core`, `suburb` |
| `locations.region` | The region the record belongs to. New Jersey records never count toward a Texas test pool (D-NJ) | e.g. `TX`, `NJ` |
| `locations.shares_address` | Whether other businesses share this address. Null until somebody checked | yes, no |
| `site_address_shares` | Which other sites share it, one row per pair, recorded from the site that was checked | — |
| `locations.site_source` | Where the site facts came from | e.g. `technician`, `desk`, `import` |
| `receiver_profiles.receiving_hours` | When the site receives. Already existed before `DE-1` | Per weekday, open and close times |
| `receiver_profiles.who_receives` | Who takes the delivery. Already existed before `DE-1` | The survey's vocabulary |

### Handoff point

One row per door, bay or counter a delivery can use. A site can have several.

| Field | Meaning | Values |
|---|---|---|
| `handoff_type` | What the delivery is handed through | `counter`, `roll_up_bay`, `loading_dock`, `side_door`, `job_site_trailer`, `mailroom` |
| `door_lat`, `door_lng` | The door pin, taken when the driver marks Delivered | Degrees |
| `stop_lat`, `stop_lng` | Where the vehicle stops, taken at geofence entry or the Arrived tap | Degrees |
| `stop_type` | Where the vehicle stops | `curb`, `lot`, `alley`, `loading_zone`, `dock_apron` |
| `stop_legal` | Whether stopping there is legal | yes, no |
| `walk_distance_m` | Walk from the stop point to the door | Metres, zero or more |
| `steps_or_ramps` | Level changes on the walk | `none`, `steps`, `ramp`, `steps_and_ramp` |
| `continuous_sidewalk` | Whether a sidewalk runs the whole way | yes, no |
| `obstructions` | What is in the way | List: gate, fence, door code, parked vehicles. Empty list means none were seen |
| `drone_open_ground` | Whether there is open ground a drone could land on | yes, no |
| `drone_ground_distance_m` | How far that ground is from the door | Metres. Optional |
| `overhead` | What is overhead near the door | List: wires, canopy, trees. Empty list means clear |
| `door_width_cm`, `dock_height_cm` | Door width and dock height | Centimetres. Optional |
| `photo_approach`, `photo_stop_point`, `photo_path`, `photo_handoff` | The four guided photos | Storage keys. All four are needed for gold |
| `source`, `collected_by`, `collected_on` | Who captured the point, how, and when | — |

### Delivery

| Field | Meaning | Values |
|---|---|---|
| `stops.handoff_point_id` | The handoff point this delivery used | — |
| `stops.record_source` | Where the record came from. Every record has one | `lmx_app` by default; e.g. `import`, `off_platform`, `desk` |
| `stops.collected_by` | Who collected the record | A driver, technician or scorer id |
| `orders.item_description` | What was carried | Free text |
| `orders.item_weight_kg` | The item's real weight. Separate from dispatch's `weight_units`, which is a unitless capacity number that defaults to 1.0 | Kilograms, zero or more |
| `orders.item_size_class` | The item's size, picked without a tape measure | `envelope`, `small`, `medium`, `large`, `oversize` |
| `orders.item_hazmat_or_liquid` | Whether the item is hazardous or a liquid | yes, no |
| Delivery profile | Urgency, a time window and the distance. Already on the order before `DE-1` | `sla_tier`; `promised_at` or `delivery_window_end`; both ends' coordinates |

### Labels

| Field | Meaning | Values |
|---|---|---|
| `machine` | Which machine the verdict is about | `drone`, `sidewalk_robot`, `car` |
| `verdict` | Could that machine have made this delivery | `yes`, `no`, `cant_tell` |
| `reason_codes` | For a "no", which checklist questions failed | List |
| `confidence` | The scorer's confidence, where given | 0 to 1. Optional |
| `scorer_id` | Who scored it | — |
| `is_blind_second` | Whether this is the blind second scorer, who cannot see the first | yes, no |
| `machine_limits_version` | Which machine limits the verdict was scored against | Required once `DE-2` lands |

---

## Tiers

A record is graded on five **elements** (D-TIER). An element counts only when
every field it needs is filled.

| Element | Needs |
|---|---|
| Pickup | The pickup's handoff point fully captured (every field above except the optional ones), and its site's kind, setting, shared-address answer, receiving hours and who receives |
| Drop-off | The same, for the drop-off end |
| Item | Description, weight in kilograms, size class, and the hazmat-or-liquid flag |
| Delivery profile | Urgency, a time window, and both ends located |
| Autonomy qualifier | A first scorer's verdict for all three machines |

| Tier | Elements missing |
|---|---|
| Gold | None |
| Silver | One |
| Bronze | Two |
| Reference | Three or more |

The tier is computed, never stored, by the `record_tiers` view, so it cannot go
stale when a field is filled in later. The source is a separate field and does
not affect the tier.

A handoff point on a location that was merged into another reads its site
facts from the location it was merged into, following the chain as identity
resolution does.

**Open:** a visit has no order, so as defined here it is missing pickup, item
and delivery profile and grades reference. The data-engine addendum expected a
visit to grade silver. `DE-6` needs that settled before it is built.
