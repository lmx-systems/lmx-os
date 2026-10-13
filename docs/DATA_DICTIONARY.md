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
| `locations.kind_of_site` | What kind of business is at the address | Taxonomy code: `independent_repair_or_tire`, `body_shop`, `dealership_counter`, `parts_store_counter`, `fleet_or_transport_yard`; outside taxonomy v0.1 so far, `municipal_fleet_garage` and `gas_station_with_bays`. Overlaps `node_class` (seven classes); the two are not reconciled yet |
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
| `stops.stop_type` | What the stop is. A record is a drop-off or a visit | `pickup`, `dropoff`, `visit` (a technician at a site, no delivery: `DE-6`) |
| `stops.record_source` | Where the record came from. Every record has one | `lmx_app` by default; `import_route_export` and `import_site_counts` for Experiment 0 (`DE-3`); `redrive` for a technician's visit (`DE-6`); `off_platform` for a gig job a driver logged (`DE-7`); later e.g. `desk` |
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
| `machine_limits_version` | Which machine limits the verdict was scored against | Required. A version with a row for this machine (below) |

### Machine limits

What each machine can carry and where it can go (`machine_limits`, `DE-2`). A
verdict is only meaningful against the limits it was scored with, and those are
not settled, so they are versioned: one row per machine per version, and a
version is never edited. A confirmed limit arrives as a new version
(`scripts/machine_limits.py`).

| Field | Meaning | Values |
|---|---|---|
| `version` | The version's name. Every label cites one | `v1` is the seed |
| `machine` | Which machine | `drone`, `sidewalk_robot`, `car` |
| `max_weight_kg` | The heaviest item it takes, inclusive | Kilograms, above zero |
| `max_size_class` | The largest size class it takes | `envelope`, `small`, `medium`, `large`, `oversize` |
| `hazmat_or_liquid_allowed` | Whether it may carry a hazardous or liquid item | yes, no |
| `needs_step_free_path` | Whether the handoff point must have no steps | yes, no |
| `needs_continuous_sidewalk` | Whether a sidewalk must run all the way to the door | yes, no |
| `needs_open_ground` | Whether it needs open, flat ground near the door to set down | yes, no |
| `max_walk_distance_m` | How far the stop may be from the door | Metres; empty means no limit |
| `basis` | How the row is known | `assumed`, `operator_reported`, `measured` |
| `source` | Where the figures came from, in words | — |

**`v1` is assumed throughout.** Drone: 2.5 kg, small, needs open ground. Sidewalk
robot: 9 kg, medium, step-free and a continuous sidewalk. Car: 25 kg, large,
stop within 50 m of the door. None takes a hazardous or liquid item. Our own
documents disagree on the drone's payload (2.5 kg against about 9 lb), and where
a limit is unknown it is set strict, because a wrong "yes" costs more than a
wrong "no".

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

**A visit is graded on its door (D-VISIT, 12 Oct).** A technician's visit
(`DE-6`) has no order, so its pickup, item and delivery profile are not
applicable: null in `record_tiers`, and not counted as missing. Its drop-off end
is the handoff point captured and the site's kind, setting and shared-address
answer - not receiving hours or who receives, which a technician who hands
nothing over cannot learn and records as unknown.

| Visit tier | Has |
|---|---|
| Gold | Drop-off end and autonomy qualifier |
| Silver | Drop-off end |
| Reference | No drop-off end |

**An off-platform delivery is graded like any delivery (`DE-7`)**, with two
readings of the delivery profile, because LMX never held the job. A driver who
delivered a gig platform's job logs it as one record: a stand-in order
(`orders.source_system = 'off_platform'`, no client, `intake_mode = 'backfill'`)
holding the item, a pickup and a drop-off stop with `record_source =
'off_platform'`, and the job pointing at the order (`gig_jobs.record_order_id`).

| Delivery profile, off-platform | Reads |
|---|---|
| Urgency | The platform's drop-off deadline (`orders.delivery_window_end`). `sla_tier` stays null: it is LMX's call on how long to hold an order, and nobody held this one |
| Pickup located | The pickup counter's site coordinates, or the driver's pin at the counter. There is no shop |

Its stops' status is `recorded` and its route's `record`: something that
happened elsewhere, written down, never LMX work. `gig_jobs.record_bonus_cents`
is the bonus owed for logging it - set once the drop-off time is, recorded and
not paid. A job typed on the record form has `gig_jobs.intake_source =
'record_form'`: the driver's word alone, checked before any bonus is paid.
