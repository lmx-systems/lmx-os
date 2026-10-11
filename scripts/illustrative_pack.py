#!/usr/bin/env python3
"""Build the illustrative analytics pack from the Experiment 0 workbook (`DE-16`).

    python scripts/illustrative_pack.py pack <workbook>
    python scripts/illustrative_pack.py geocode <workbook> --out ml/out/exp0_coordinates.csv
    python scripts/illustrative_pack.py pack <workbook> --coordinates ml/out/exp0_coordinates.csv \\
        --hub 40.89,-74.01 --radius-km 8

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
`ml/out/`, which is gitignored. Drop-offs only throughout: the rows at supplier
counters are pickups (`DE-3`). Outputs go to `--out` (default `ml/out/illustrative/`).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
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
from scripts.import_experiment_0 import parse_workbook  # noqa: E402

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

    args = parser.parse_args()
    if args.command == "geocode":
        return asyncio.run(_geocode(args.workbook, args.out))
    return _pack(args.workbook, args.out, args.coordinates, args.hub, args.radius_km)


if __name__ == "__main__":
    raise SystemExit(main())
