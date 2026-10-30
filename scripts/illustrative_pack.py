#!/usr/bin/env python3
"""Build the illustrative analytics pack from the Experiment 0 workbook (`DE-16`).

    python scripts/illustrative_pack.py pack <workbook>
    python scripts/illustrative_pack.py geocode <workbook> --out ml/out/exp0_coordinates.csv
    python scripts/illustrative_pack.py pack <workbook> --coordinates ml/out/exp0_coordinates.csv \\
        --hub 40.89,-74.01 --radius-km 8
    python scripts/illustrative_pack.py locate <workbook> --out ml/out/exp0_coordinates.csv
    python scripts/illustrative_pack.py zones <workbook> --coordinates ml/out/exp0_coordinates.csv \\
        --zones ml/analytics/zones_austin.geojson --relocate-to 30.5086,-97.6788
    python scripts/illustrative_pack.py eligibility <workbook> --limits ml/out/machine_limits.json \\
        --assumptions ml/modality/assumption_sheet_a1.json --sample 300 \\
        [--coordinates ... --zones ... --relocate-to ...]
    python scripts/illustrative_pack.py supplier-receivers --out ml/out/supplier_b_receivers.csv
    python scripts/illustrative_pack.py supplier <workbook> --receivers ml/out/supplier_b_receivers.csv \
        --coordinates ml/out/exp0_coordinates.csv --relocate-to 30.5086,-97.6788

Rich's three readings (5 Oct): deliveries by hour of day, the share of drop-offs
within a drone radius of a hub, and the spread of distances. Every chart is
stamped illustrative (D-SIM) and is never quoted as model accuracy.

**The hub and the radius have no defaults.** Experiment 0 names no depot, and LMX
has no New Jersey hub, so where a drone would launch from is a choice, as is how
far it flies; both are printed on the charts that use them. Without them, and
without coordinates for the sites, the pack holds the hour-of-day chart alone and
says what the other two need.

`geocode` resolves the sites' addresses with the app's configured geocoder and
writes site id, latitude and longitude - no names or addresses - to a file under
`ml/out/`, which is gitignored. **`locate` writes the same file without sending
an address anywhere** (D-DESK): it matches the sites against the open address
points `scripts/desk_prefill.py fetch` caches. Drop-offs only throughout: the rows
at supplier counters are pickups (`DE-3`). Outputs go to `--out` (default
`ml/out/illustrative/`).

## The partner mock-up (the Proof Map, 18 Oct)

`zones` moves the located drop-offs to another city, keeping each one's distance
and bearing from the footprint's centre (the Proof Map's first step: the design
partner's orders relocated to Round Rock), lays the operator zones in a GeoJSON
file over them, and counts what falls inside each, by hour (`ml/analytics/zones.py`).
The target has no default: where the footprint lands is a choice, printed on
every output. It writes a map, an hour chart per zone, `zones.md`, and the moved
drop-offs as GeoJSON (hour only) for a map tool.

`eligibility` scores a seeded sample of drop-offs with the checklist twice - as
measured, and with an assumption sheet filling the gaps - and ranks the
assumptions by how many verdicts rest on each (`ml/modality/assumed.py`): the
Proof Map's O1 and its list of facts to collect. Given the zone arguments too,
it counts the eligible drop-offs inside each zone. `--limits` is a machine
limits version as `scripts/machine_limits.py export` writes it.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.analytics.illustrative import (  # noqa: E402
    STAMP,
    bar_chart_svg,
    distance_bins,
    hour_of_day_counts,
    within_radius,
)
from ml.analytics.zones import (  # noqa: E402
    centroid,
    contains,
    count_in_zones,
    displacement,
    drops_geojson,
    load_zones,
    map_svg,
    relocate,
)
from ml.analytics.supplier_b import Receiver, density, generate, summary  # noqa: E402
from ml.modality.assumed import draw_sample, render_markdown  # noqa: E402
from ml.modality.assumed import run as run_eligibility  # noqa: E402
from scripts.import_experiment_0 import KIND_OF_SITE, REGION, parse_workbook  # noqa: E402

DISTANCE_BAND_KM = 2.0
DISTANCE_BANDS = 12


def _dropoffs(workbook):
    """(delivery, site) for every delivery row at a drop-off site."""
    sites = {s.account: s for s in workbook.sites if s.account}
    return [(d, sites.get(d.account)) for d in workbook.deliveries if not (sites.get(d.account) and sites[d.account].is_pickup)]


def _delivered_at(delivery) -> datetime | None:
    try:
        return datetime.strptime(f"{delivery.date} {delivery.delivered}", "%b %d %Y %I:%M %p")
    except (TypeError, ValueError):
        return None


def _pack(workbook_path: Path, out: Path, coordinates: Path | None, hub: str | None, radius_km: float | None) -> int:
    workbook = parse_workbook(workbook_path)
    dropoffs = _dropoffs(workbook)
    times = [t for t in (_delivered_at(d) for d, _ in dropoffs) if t is not None]
    out.mkdir(parents=True, exist_ok=True)
    first, last = min(times), max(times)
    span = f"{first:%d %b} to {last:%d %b %Y}"

    hours = hour_of_day_counts(times)
    (out / "deliveries_by_hour.svg").write_text(
        bar_chart_svg(
            title="Drop-offs by hour of day",
            subtitle=f"{len(times):,} drop-offs, {span}, one distributor's route export, New Jersey. Hour of the driver's Delivered tap.",
            labels=[f"{h:02d}" for h in range(24)],
            values=hours,
            unit="drop-offs",
        )
    )
    busiest = max(range(24), key=hours.__getitem__)
    lines = [
        "# Experiment 0 - illustrative analytics",
        "",
        f"**{STAMP}.** One distributor, {span}, New Jersey. Never quoted as model accuracy.",
        "",
        "## Drop-offs by hour of day",
        "",
        f"`deliveries_by_hour.svg`. {len(times):,} drop-offs; the busiest hour starts at {busiest:02d}:00 "
        f"({hours[busiest]:,}, {hours[busiest] / len(times):.0%}). Times are taps, so the hour is reliable and "
        "the minutes are not.",
        "",
    ]

    if coordinates and hub and radius_km:
        hub_point = tuple(float(x) for x in hub.split(","))
        located = {row["site_id"]: (float(row["lat"]), float(row["lng"])) for row in csv.DictReader(coordinates.open())}
        points = [located[s.site_id] for _, s in dropoffs if s is not None and s.site_id in located]
        unlocated = len(dropoffs) - len(points)
        near = within_radius(points, hub_point, radius_km)
        (out / "distance_from_hub.svg").write_text(
            bar_chart_svg(
                title="Drop-offs by distance from the hub",
                subtitle=f"{len(points):,} located drop-offs, {DISTANCE_BAND_KM:g} km bands from an assumed hub at {hub}; "
                f"the last band is {DISTANCE_BAND_KM * (DISTANCE_BANDS - 1):g} km and beyond.",
                labels=[f"{i * DISTANCE_BAND_KM:g}" for i in range(DISTANCE_BANDS)],
                values=distance_bins(points, hub_point, DISTANCE_BAND_KM, DISTANCE_BANDS),
                unit="drop-offs",
            )
        )
        lines += [
            "## Within a drone radius of a hub",
            "",
            f"**{near / len(points):.0%}** of located drop-offs ({near:,} of {len(points):,}) are within "
            f"{radius_km:g} km of an assumed hub at {hub}. The hub and the radius are choices, not facts. "
            f"{unlocated:,} drop-offs have no coordinates and are left out. Distance only: no payload or site check.",
            "",
            "## Distance from the hub",
            "",
            "`distance_from_hub.svg`.",
        ]
    else:
        lines += [
            "## Within a drone radius, and distance spread",
            "",
            "Not computed. Both need an assumed hub location and, for the first, a radius - neither has a default, "
            "because Experiment 0 names no depot - and coordinates for the sites (`geocode`).",
        ]
    (out / "README.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


async def _geocode(workbook_path: Path, out: Path) -> int:
    from app.geocoding import get_geocoder

    geocoder = get_geocoder()
    sites = [s for s in parse_workbook(workbook_path).sites if s.address and not s.is_pickup]
    out.parent.mkdir(parents=True, exist_ok=True)
    found = 0
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["site_id", "lat", "lng"])
        for site in sites:
            result = await geocoder.geocode(site.address)
            if result is not None:
                writer.writerow([site.site_id, result.lat, result.lng])
                found += 1
    print(f"located {found} of {len(sites)} drop-off sites with an address; wrote {out}")
    return 0


def _point(text: str) -> tuple[float, float]:
    lat, lng = (float(x) for x in text.split(","))
    return lat, lng


def _located_dropoffs(workbook, coordinates: Path) -> tuple[list, int]:
    """(delivery, site, point) for every drop-off whose site has coordinates, and how many have none."""
    located = {row["site_id"]: (float(row["lat"]), float(row["lng"])) for row in csv.DictReader(coordinates.open())}
    out, missing = [], 0
    for delivery, site in _dropoffs(workbook):
        if site is not None and site.site_id in located:
            out.append((delivery, site, located[site.site_id]))
        else:
            missing += 1
    return out, missing


def _moved(points: list, target: tuple[float, float]) -> tuple[list, tuple[float, float]]:
    origin = centroid(points)
    return relocate(points, origin, target), origin


def _footprint_km(points: list, centre: tuple[float, float], share: float = 0.9) -> float:
    """The radius from the centre that holds `share` of the points."""
    distances = sorted(sum(x * x for x in displacement(centre, p)) ** 0.5 for p in points)
    return distances[min(len(distances) - 1, int(share * len(distances)))]


def _locate(workbook_path: Path, out: Path) -> int:
    from scripts.desk_prefill import locate_addresses

    sites = [(s.site_id, s.address) for s in parse_workbook(workbook_path).sites if s.address and not s.is_pickup]
    located = locate_addresses(REGION, sites)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["site_id", "lat", "lng"])
        for site_id, (lat, lng) in sorted(located.items()):
            writer.writerow([site_id, f"{lat:.6f}", f"{lng:.6f}"])
    print(f"located {len(located)} of {len(sites)} drop-off sites with an address, on this machine; wrote {out}")
    return 0


def _zones(workbook_path: Path, out: Path, coordinates: Path, zones_path: Path, target_text: str) -> int:
    workbook = parse_workbook(workbook_path)
    rows, unlocated = _located_dropoffs(workbook, coordinates)
    zones = load_zones(json.loads(zones_path.read_text()))
    target = _point(target_text)
    points, _ = _moved([p for _, _, p in rows], target)
    hours = [t.hour if (t := _delivered_at(d)) else None for d, _, _ in rows]
    counts = count_in_zones(list(zip(points, hours)), zones)
    radius = _footprint_km(points, target)
    out.mkdir(parents=True, exist_ok=True)

    moved_to = f"centred on {target_text}"
    (out / "zones_map.svg").write_text(
        map_svg(
            title="One distributor's drop-offs, moved to the Austin metro, under operator zones",
            subtitle=f"{len(points):,} drop-offs at their New Jersey distances and bearings, {moved_to}. "
            "Zones as read from public sources.",
            points=points,
            zones=zones,
        )
    )
    (out / "relocated_dropoffs.geojson").write_text(json.dumps(drops_geojson(points, hours)) + "\n")
    lines = [
        "# Operator zones over a relocated footprint",
        "",
        f"**{STAMP}.** One distributor's drop-offs from Experiment 0, moved to the Austin metro: each keeps its "
        f"distance and bearing from the footprint's centre, which is placed at {target_text}. The streets, and so "
        "every site fact, stay in New Jersey; only the shape travels. The zones are operators' areas as we read "
        "them, not polygons they gave us.",
        "",
        f"{len(points):,} located drop-offs; {unlocated:,} more have no coordinates and are left out. "
        f"90% lie within {radius:.1f} km of the centre. Map: `zones_map.svg`; the points: `relocated_dropoffs.geojson`.",
        "",
        "| Zone | Machine | Drop-offs inside | Share of all | Busiest hour inside |",
        "|---|---|---|---|---|",
    ]
    for i, (zone, count) in enumerate(zip(zones, counts)):
        busiest = max(range(24), key=count.by_hour.__getitem__) if count.inside else None
        lines.append(
            f"| {zone.name} | {zone.machine or '-'} | {count.inside:,} | {count.share:.0%} | "
            f"{f'{busiest:02d}:00' if busiest is not None else '-'} |"
        )
        if count.inside:
            (out / f"zone_{i + 1}_by_hour.svg").write_text(
                bar_chart_svg(
                    title=f"Drop-offs inside {zone.name}, by hour",
                    subtitle=f"{count.inside:,} of {len(points):,} relocated drop-offs, {moved_to}. Hour of the Delivered tap.",
                    labels=[f"{h:02d}" for h in range(24)],
                    values=count.by_hour,
                    unit="drop-offs",
                )
            )
    lines += ["", "Zone notes:", ""] + [f"- **{z.name}.** {z.note}" for z in zones if z.note]
    (out / "zones.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def _eligibility(args) -> int:
    workbook = parse_workbook(args.workbook)
    limits = json.loads(args.limits.read_text())
    sheet = json.loads(args.assumptions.read_text())
    zone_args = (args.coordinates, args.zones, args.relocate_to)
    if any(zone_args) and not all(zone_args):
        raise SystemExit("zones need all three of --coordinates, --zones and --relocate-to")

    moved: dict[int, tuple[float, float]] = {}
    zones = []
    if all(zone_args):
        rows, _ = _located_dropoffs(workbook, args.coordinates)
        points, _ = _moved([p for _, _, p in rows], _point(args.relocate_to))
        moved = {id(d): p for (d, _, _), p in zip(rows, points)}
        zones = load_zones(json.loads(args.zones.read_text()))

    records = []
    for i, (delivery, site) in enumerate(_dropoffs(workbook)):
        kind = KIND_OF_SITE.get(site.kind_label) if site else None
        at = _delivered_at(delivery)
        point = moved.get(id(delivery))
        records.append({
            "stop_id": f"exp0-{i}",
            "stop_type": "dropoff",
            "kind_of_site": kind,
            "group": [kind, None],
            # No piece count is zero pieces delivered: it is unknown.
            "pieces": delivery.pieces or None,
            "hour": at.hour if at else None,
            # The export's weight column is 0 on every row: unweighed, not weightless.
            "item": {"weight_kg": None, "size_class": None, "hazmat_or_liquid": None},
            "handoff": {},
            "site": {"receiving_hours": None, "who_receives": None},
            "zones": [z.name for z in zones if point and contains(z, point)],
        })
    sample = draw_sample(records, args.sample, args.seed)
    report = run_eligibility(sample, limits, sheet, {z.name: z.machine for z in zones} or None)
    about = (
        f"A seeded sample (seed {args.seed}) of {len(sample)} of {len(records):,} drop-offs from one distributor's "
        "route export in Experiment 0, drawn in proportion to kind of site. No item was weighed and no door "
        "surveyed, so as measured decides nothing; as assumed is the assumption sheet's answer, not a measurement."
    )
    if zones:
        about += f" Zones count the sample's drop-offs moved to the Austin metro, centred on {args.relocate_to}."
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "eligibility.json").write_text(json.dumps(report, indent=2) + "\n")
    text = render_markdown(report, title="Eligibility on a sample - as measured and as assumed", about=about)
    (args.out / "eligibility.md").write_text(text)
    print(text)
    return 0


# Overture Places, the release the receivers were first drawn from (30 Oct 2026).
SUPPLIER_B_RELEASE = "2026-10-21.0"
# Round Rock, Pflugerville, the Cedar Park edge and north Austin: (min lng, min lat, max lng, max lat).
SUPPLIER_B_BOX = (-97.85, 30.30, -97.55, 30.65)


def _supplier_receivers(out: Path, spec_path: Path, release: str) -> int:
    """Fetch the receivers from Overture Places: id, category, confidence, point. No names."""
    from scripts.desk_prefill import _duckdb

    spec = json.loads(spec_path.read_text())
    categories = ", ".join(f"'{c}'" for c in spec["categories"])
    xmin, ymin, xmax, ymax = SUPPLIER_B_BOX
    con = _duckdb()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""COPY (SELECT id, taxonomy.primary AS category, confidence,
                         (bbox.ymin + bbox.ymax) / 2 AS lat, (bbox.xmin + bbox.xmax) / 2 AS lng
                  FROM read_parquet('s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*',
                                    hive_partitioning = 1)
                  WHERE bbox.xmin BETWEEN {xmin} AND {xmax} AND bbox.ymin BETWEEN {ymin} AND {ymax}
                    AND taxonomy.primary IN ({categories})
                  ORDER BY id) TO '{out}' (HEADER)"""
    )
    rows = sum(1 for _ in out.open()) - 1
    print(f"wrote {rows} places in the spec's categories from Overture {release} to {out}")
    return 0


def _supplier(args) -> int:
    spec = json.loads(args.spec.read_text())
    receivers = [
        Receiver(row["id"], row["category"], float(row["lat"]), float(row["lng"]))
        for row in csv.DictReader(args.receivers.open())
        if float(row["confidence"]) >= spec["min_confidence"]
    ]
    workbook = parse_workbook(args.workbook)
    times = [t for t in (_delivered_at(d) for d, _ in _dropoffs(workbook)) if t]
    orders = generate(receivers, spec, hour_of_day_counts(times), args.seed)

    rows, _ = _located_dropoffs(workbook, args.coordinates)
    points, _ = _moved([p for _, _, p in rows], _point(args.relocate_to))
    a = [(point, at.hour) for (delivery, _, _), point in zip(rows, points) if (at := _delivered_at(delivery))]
    days_a = len({at.date() for at in times})
    b = [((o.lat, o.lng), o.hour) for o in orders]
    reading = density(a, b, days_a, spec["business_days"])
    # The whole day as one hour: at this volume a cell-hour holds a fraction of a drop.
    reading["day"] = density([(p, 0) for p, _ in a], [(p, 0) for p, _ in b], days_a, spec["business_days"])["hours"][0]

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "supplier_b_orders.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["order_id", "day", "hour", "minute", "receiver_id", "category", "lat", "lng", "weight_kg"])
        for o in orders:
            writer.writerow([o.order_id, o.day, o.hour, o.minute, o.receiver_id, o.category, o.lat, o.lng, o.weight_kg])
    about = summary(orders)
    (args.out / "supplier_b.json").write_text(json.dumps({"summary": about, "density": reading}, indent=1))
    (args.out / "supplier_b.md").write_text(_supplier_markdown(spec, about, reading, days_a, args.relocate_to))
    print(f"{about['orders']} orders to {about['receivers']} receivers; wrote {args.out}/supplier_b.md")
    return 0


def _supplier_markdown(spec: dict, about: dict, reading: dict, days_a: int, relocate_to: str) -> str:
    p25, p50, p75, p90 = about["weight_kg_p25_p50_p75_p90"]
    lines = [
        "# Supplier B and the design partner, together",
        "",
        f"**{STAMP}.** {spec['note']}",
        "",
        f"{about['orders']} orders over {spec['business_days']} business days to {about['receivers']} receivers. "
        f"Weight: median {p50} kg (25th {p25}, 75th {p75}, 90th {p90}). "
        f"Under 2.3 kg (drone, limits v2): {about['share_at_most_2_3_kg']:.0%}; under 9.07 kg (robot): "
        f"{about['share_at_most_9_07_kg']:.0%}; under 25 kg (car): {about['share_at_most_25_kg']:.0%}.",
        "",
        f"## Drops per {reading['cell_square_miles']} square mile cell, by hour (per business day)",
        "",
        f"The design partner's located drop-offs over {days_a} days, moved to centre on {relocate_to}, beside "
        "Supplier B's. A shared cell has drops from both in that hour.",
        "",
        "| Hour | Design partner drops | Supplier B drops | Cells, partner | Cells, B | Shared cells | "
        "Drops a cell, partner alone | Drops a cell, together | B drops in shared cells |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for h in [{**reading["day"], "hour": None}, *reading["hours"]]:
        when = "whole day" if h["hour"] is None else f"{h['hour']:02d}:00"
        lines.append(
            f"| {when} | {h['drops_a']} | {h['drops_b']} | {h['cells_a']} | {h['cells_b']} | "
            f"{h['cells_shared']} | {h['drops_per_cell_a']} | {h['drops_per_cell_together']} | "
            f"{h['b_drops_in_shared_cells']} |"
        )
    lines += ["", "## Where each input comes from", ""]
    for name, category in spec["categories"].items():
        lines.append(f"- **{name}** ({category['share']:.0%}): {category['source']}")
    lines += [
        f"- **Orders a day** ({spec['orders_per_business_day']['value']}): {spec['orders_per_business_day']['source']}",
        f"- **Weight**: {spec['weight_lb_quantiles']['source']}",
        f"- **Hour of day**: {spec['hours_source']}",
        f"- **Receivers' order rates**: {spec['activity_source']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("pack")
    pack.add_argument("workbook", type=Path)
    pack.add_argument("--out", type=Path, default=Path("ml/out/illustrative"))
    pack.add_argument("--coordinates", type=Path)
    pack.add_argument("--hub", help="LAT,LNG of an assumed hub; no default")
    pack.add_argument("--radius-km", type=float, help="the drone's radius; no default")
    geocode = sub.add_parser("geocode")
    geocode.add_argument("workbook", type=Path)
    geocode.add_argument("--out", type=Path, default=Path("ml/out/exp0_coordinates.csv"))
    locate = sub.add_parser("locate")
    locate.add_argument("workbook", type=Path)
    locate.add_argument("--out", type=Path, default=Path("ml/out/exp0_coordinates.csv"))
    zones = sub.add_parser("zones")
    zones.add_argument("workbook", type=Path)
    zones.add_argument("--coordinates", type=Path, required=True)
    zones.add_argument("--zones", type=Path, required=True, help="GeoJSON operator zones")
    zones.add_argument("--relocate-to", required=True, help="LAT,LNG where the footprint's centre lands; no default")
    zones.add_argument("--out", type=Path, default=Path("ml/out/illustrative"))
    eligibility = sub.add_parser("eligibility")
    eligibility.add_argument("workbook", type=Path)
    eligibility.add_argument("--limits", type=Path, required=True, help="a machine limits version, as exported")
    eligibility.add_argument("--assumptions", type=Path, required=True, help="the assumption sheet")
    eligibility.add_argument("--sample", type=int, default=300)
    eligibility.add_argument("--seed", type=int, default=20261022)
    eligibility.add_argument("--coordinates", type=Path)
    eligibility.add_argument("--zones", type=Path)
    eligibility.add_argument("--relocate-to")
    eligibility.add_argument("--out", type=Path, default=Path("ml/out/illustrative"))

    receivers = sub.add_parser("supplier-receivers")
    receivers.add_argument("--out", type=Path, default=Path("ml/out/supplier_b_receivers.csv"))
    receivers.add_argument("--spec", type=Path, default=Path("ml/analytics/supplier_b_spec.json"))
    receivers.add_argument("--release", default=SUPPLIER_B_RELEASE)
    supplier = sub.add_parser("supplier")
    supplier.add_argument("workbook", type=Path)
    supplier.add_argument("--receivers", type=Path, required=True)
    supplier.add_argument("--spec", type=Path, default=Path("ml/analytics/supplier_b_spec.json"))
    supplier.add_argument("--coordinates", type=Path, required=True)
    supplier.add_argument("--relocate-to", required=True, help="LAT,LNG where the partner's footprint lands")
    supplier.add_argument("--seed", type=int, default=20261030)
    supplier.add_argument("--out", type=Path, default=Path("ml/out/illustrative"))

    args = parser.parse_args()
    if args.command == "supplier-receivers":
        return _supplier_receivers(args.out, args.spec, args.release)
    if args.command == "supplier":
        return _supplier(args)
    if args.command == "geocode":
        return asyncio.run(_geocode(args.workbook, args.out))
    if args.command == "locate":
        return _locate(args.workbook, args.out)
    if args.command == "zones":
        return _zones(args.workbook, args.out, args.coordinates, args.zones, args.relocate_to)
    if args.command == "eligibility":
        return _eligibility(args)
    return _pack(args.workbook, args.out, args.coordinates, args.hub, args.radius_km)


if __name__ == "__main__":
    raise SystemExit(main())
