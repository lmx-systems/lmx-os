#!/usr/bin/env python3
"""Fill sites' kind, setting and shared-address answer from open data (`DE-15`), and report the counts.

    python scripts/desk_prefill.py fetch                     # bulk files for the state, once
    python scripts/desk_prefill.py run --region NJ           # dry run
    python scripts/desk_prefill.py run --region NJ --commit

The rules are in `app/record/desk_prefill.py`. This script does the reading.

## Nothing about a site is sent anywhere (D-DESK)

`fetch` downloads whole-state files and nothing else: Overture's places and
address points inside the state's bounding box, and the 2020 Census blocks with
their housing counts. The bounding box is the state's, not a site's, so the
requests say which state we care about and nothing more. `run` matches the
sites against those files on this machine. Neither sends an address or a
coordinate of ours to any service - in particular not the app's geocoder, whose
default is the public Nominatim. Whether that may change is Sourabh's call.

`fetch` needs DuckDB with its spatial extension (`requirements-ml.txt`; the API
image does not carry it). The files land in `ml/cache/desk_prefill/`, which is
gitignored; they are public data, but several hundred megabytes of it.

## What `run` writes

For every canonical location in the region, the three site facts, each only
where it is empty, marked `desk` in `locations.site_fact_sources`. It never
changes a coordinate, an address or anything a person or the import set. Like
the other data-engine scripts it **prints counts, never rows**, and a dry run is
the default and rolls back. Running it again fills only what is still empty.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.location import Location  # noqa: E402
from app.record.desk_prefill import (  # noqa: E402
    FACTS,
    Place,
    PrefillReport,
    SiteEvidence,
    StreetAddress,
    apply_desk_facts,
    evidence_for_site,
    normalize_street,
    parse_site_address,
    parse_street_line,
)

CACHE = Path(__file__).resolve().parent.parent / "ml" / "cache" / "desk_prefill"
OVERTURE_RELEASE = "2026-09-23.1"
OVERTURE = "s3://overturemaps-us-west-2/release/{release}/theme={theme}/type={type}/*"
# Per region: the state's bounding box (min lng, min lat, max lng, max lat), its
# postal code and its Census FIPS code. A box, so a neighbouring state's edge
# comes too; matching is by ZIP and street, so that costs only disk.
REGIONS = {
    "NJ": {"bbox": (-75.6, 38.9, -73.88, 41.36), "state": "NJ", "fips": "34"},
}
CENSUS_BLOCKS = "https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_{fips}_tabblock20.zip"


def _files(region: str) -> dict[str, Path]:
    return {
        "places": CACHE / f"places_{region}.parquet",
        "addresses": CACHE / f"addresses_{region}.parquet",
        "blocks": CACHE / f"blocks_{region}.parquet",
    }


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit("DuckDB is needed: install requirements-ml.txt (the .venv-ml interpreter)") from exc
    return duckdb.connect()


# --- fetch -------------------------------------------------------------------
def fetch(region: str, release: str) -> int:
    import urllib.request

    spec = REGIONS[region]
    xmin, ymin, xmax, ymax = spec["bbox"]
    files = _files(region)
    CACHE.mkdir(parents=True, exist_ok=True)
    con = _duckdb()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2';")
    inside = f"bbox.xmin BETWEEN {xmin} AND {xmax} AND bbox.ymin BETWEEN {ymin} AND {ymax}"

    # No names, phones or websites: the rules need a category and an address.
    con.execute(f"""
        COPY (
            SELECT ST_X(geometry) AS lng, ST_Y(geometry) AS lat, confidence, operating_status,
                   taxonomy.primary AS category, taxonomy.alternates AS alternates,
                   addresses[1].freeform AS street, substr(addresses[1].postcode, 1, 5) AS postcode
            FROM read_parquet('{OVERTURE.format(release=release, theme="places", type="place")}', hive_partitioning=1)
            WHERE {inside}
        ) TO '{files["places"]}' (FORMAT parquet)""")
    con.execute(f"""
        COPY (
            SELECT number, street, substr(postcode, 1, 5) AS postcode, address_levels[1].value AS state,
                   ST_X(geometry) AS lng, ST_Y(geometry) AS lat
            FROM read_parquet('{OVERTURE.format(release=release, theme="addresses", type="address")}', hive_partitioning=1)
            WHERE country = 'US' AND {inside}
        ) TO '{files["addresses"]}' (FORMAT parquet)""")

    blocks_zip = CACHE / f"tl_2020_{spec['fips']}_tabblock20.zip"
    if not blocks_zip.exists():
        urllib.request.urlretrieve(CENSUS_BLOCKS.format(fips=spec["fips"]), blocks_zip)  # noqa: S310 - a fixed Census URL
    con.execute(f"""
        COPY (
            SELECT CAST(INTPTLAT20 AS DOUBLE) AS lat, CAST(INTPTLON20 AS DOUBLE) AS lng, HOUSING20 AS housing_units
            FROM ST_Read('/vsizip/{blocks_zip}/{blocks_zip.stem}.shp') WHERE HOUSING20 > 0
        ) TO '{files["blocks"]}' (FORMAT parquet)""")

    for name, path in files.items():
        (count,) = con.execute(f"SELECT count(*) FROM '{path}'").fetchone()
        print(f"{name:<10} {count:>9} rows")
    print(f"Overture release {release}; Census 2020 blocks. Cached in {CACHE.relative_to(CACHE.parents[2])}")
    return 0


# --- run ---------------------------------------------------------------------
def _number(value: str | None) -> str | None:
    """A house number as the street address parser keeps it: leading digits and an optional letter."""
    if not value:
        return None
    parsed = parse_street_line(f"{value} x", "00000")
    return parsed.number if parsed else None


def _address_points(con, region: str, addresses) -> dict[StreetAddress, list[tuple[float, float]]]:
    """Overture's address points in the ZIPs these addresses name, by number, street and ZIP."""
    files = _files(region)
    zips = sorted({a.postcode for a in addresses if a is not None})
    zip_list = ", ".join(f"'{z}'" for z in zips) or "''"
    points: dict[StreetAddress, list[tuple[float, float]]] = defaultdict(list)
    for number, street, postcode, lat, lng in con.execute(
        f"SELECT number, street, postcode, lat, lng FROM '{files['addresses']}' "
        f"WHERE state = ? AND postcode IN ({zip_list})",
        [REGIONS[region]["state"]],
    ).fetchall():
        n = _number(number)
        if n and street:
            points[StreetAddress(n, normalize_street(street), postcode)].append((lat, lng))
    return points


def locate_addresses(region: str, sites: list[tuple[str, str]]) -> dict[str, tuple[float, float]]:
    """Coordinates for sites' addresses, matched against the cached address points
    on this machine - the same exact match as the evidence, so nothing is sent to
    a geocoder (D-DESK). `sites` is (site id, address) pairs; an address with no
    match is left out, and a building with several points takes their middle."""
    files = _files(region)
    if not files["addresses"].exists():
        raise SystemExit(f"Not fetched yet: {files['addresses'].name}. Run `scripts/desk_prefill.py fetch` first.")
    parsed = {site_id: parse_site_address(address) for site_id, address in sites}
    points = _address_points(_duckdb(), region, parsed.values())
    located = {}
    for site_id, address in parsed.items():
        found = points.get(address) if address else None
        if found:
            located[site_id] = (sum(p[0] for p in found) / len(found), sum(p[1] for p in found) / len(found))
    return located


def gather_evidence(region: str, sites: list[tuple[object, str]]) -> list[SiteEvidence]:
    """Each site's evidence from the cached files. `sites` is (location id, address) pairs; nothing is printed."""
    files = _files(region)
    missing = [str(p.name) for p in files.values() if not p.exists()]
    if missing:
        raise SystemExit(f"Not fetched yet: {', '.join(missing)}. Run `fetch` first.")
    parsed = {loc_id: parse_site_address(address) for loc_id, address in sites}
    con = _duckdb()
    points = _address_points(con, region, parsed.values())

    # Places by number and street in any ZIP (`evidence_for_site` decides which
    # count); the house numbers narrow the scan before Python parses addresses.
    numbers = sorted({a.number.rstrip("abcdefghijklmnopqrstuvwxyz") for a in parsed.values() if a is not None})
    number_list = ", ".join(f"'{n}'" for n in numbers) or "''"
    places: dict[tuple[str, str], list[Place]] = defaultdict(list)
    for street, postcode, category, alternates, confidence, status, lat, lng in con.execute(
        f"SELECT street, postcode, category, alternates, confidence, operating_status, lat, lng "
        f"FROM '{files['places']}' WHERE regexp_extract(street, '^\\s*(\\d+)', 1) IN ({number_list})"
    ).fetchall():
        key = parse_street_line(street or "", postcode or "00000")
        if key is not None:
            places[(key.number, key.street)].append(
                Place(category, tuple(alternates or ()), confidence, status, lat, lng, postcode)
            )

    blocks = con.execute(f"SELECT lat, lng, housing_units FROM '{files['blocks']}'").fetchall()
    return [
        evidence_for_site(
            loc_id,  # type: ignore[arg-type]
            address,
            points.get(address, []) if address else [],
            places.get((address.number, address.street), []) if address else [],
            blocks,
        )
        for loc_id, address in parsed.items()
    ]


# The view is computed whole and then joined: joined to `locations` directly,
# Postgres re-evaluates it per site and a count over one region took minutes.
_BY_REGION = (
    "WITH rt AS MATERIALIZED (SELECT site_id, tier, desk_site FROM record_tiers) "
    "SELECT {select} FROM rt JOIN locations l ON l.id = rt.site_id WHERE l.region = :region {where}"
)


async def _tiers(session, region: str) -> dict[str, int]:
    rows = await session.execute(
        text(_BY_REGION.format(select="rt.tier, count(*)", where="GROUP BY rt.tier")), {"region": region}
    )
    return dict(rows.all())


async def _count(session, region: str, where: str) -> int:
    rows = await session.execute(text(_BY_REGION.format(select="count(*)", where=where)), {"region": region})
    return int(rows.scalar_one())


async def _records_at_known_sites(session, region: str) -> int:
    """Records whose site now has all three place facts: the ones a captured door could lift."""
    return await _count(
        session, region,
        "AND l.kind_of_site IS NOT NULL AND l.setting IS NOT NULL AND l.shares_address IS NOT NULL",
    )


async def run(region: str, commit: bool) -> int:
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            sites = [
                (row.id, row.address)
                for row in (
                    await session.execute(
                        select(Location.id, Location.address).where(
                            Location.region == region, Location.merged_into_id.is_(None)
                        )
                    )
                ).all()
            ]
            known_before = await _records_at_known_sites(session, region)
            tiers_before = await _tiers(session, region)
            evidence = gather_evidence(region, sites)
            report = await apply_desk_facts(session, evidence)
            known_after = await _records_at_known_sites(session, region)
            tiers_after = await _tiers(session, region)
            desk_capped = await _count(session, region, "AND rt.desk_site")
            if commit:
                await session.commit()
            else:
                await session.rollback()
    finally:
        await engine.dispose()
    _print(report, region, known_before, known_after, tiers_before, tiers_after, desk_capped, commit)
    return 0


def _print(
    report: PrefillReport, region: str, known_before: int, known_after: int,
    tiers_before: dict, tiers_after: dict, desk_capped: int, committed: bool,
) -> None:
    print(f"region {region}: {report.sites} locations, {report.matched} found in open data")
    print(f"{'fact':<16}{'filled':>8}{'known':>8}{'agrees':>8}{'differs':>9}{'unknown':>9}")
    for name in FACTS:
        print(
            f"{name:<16}{report.filled[name]:>8}{report.already_known[name]:>8}"
            f"{report.agrees[name]:>8}{report.disagrees[name]:>9}{report.unknown[name]:>9}"
        )
    print(f"locations with all three place facts after   {report.place_known_after}")
    print(f"records at such sites                        {known_before} before, {known_after} after")
    print(f"records by tier                              {tiers_before or '-'} before, {tiers_after or '-'} after")
    print(f"records whose grade a desk fact caps         {desk_capped}")
    print("committed" if committed else "dry run: rolled back; --commit to keep")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="download the region's bulk files")
    f.add_argument("--region", default="NJ", choices=sorted(REGIONS))
    f.add_argument("--release", default=OVERTURE_RELEASE, help="Overture release")
    r = sub.add_parser("run", help="match the region's sites and fill what is empty")
    r.add_argument("--region", default="NJ", choices=sorted(REGIONS))
    r.add_argument("--commit", action="store_true", help="keep what was written")
    args = parser.parse_args()
    if args.command == "fetch":
        return fetch(args.region, args.release)
    return asyncio.run(run(args.region, args.commit))


if __name__ == "__main__":
    raise SystemExit(main())
