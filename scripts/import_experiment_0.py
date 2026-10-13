#!/usr/bin/env python3
"""Load the Experiment 0 workbook as golden records (`DE-3`), and report the counts.

    python scripts/import_experiment_0.py path/to/experiment-0.xlsx            # dry run
    python scripts/import_experiment_0.py path/to/experiment-0.xlsx --commit

Experiment 0 is what our existing New Jersey delivery data covers: a Sites tab
(one row per business, from two sources), and a delivery-rows tab for the source
whose route exports we hold. The other source's deliveries exist only as a count
per site. **The workbook stays on the shared drive** - it carries customer data -
and this script reads it from wherever it is put. Like
`load_identity_from_export.py`, it **prints counts, never rows**, and a dry run
is the default and rolls back.

## What it writes

    Site            a `locations` row per site with an address, region NJ, its kind
                    of site (guessed from the business name, so `site_source`
                    `import`), and whether it shares its address. A site with no
                    address cannot be a location and its records have no site
    Handoff point   one per location, every field empty: the site has a door, and
                    nothing is known about it. It is what links a record to its
                    site, since an imported delivery has no order
    Delivery        a completed stop per delivery row, and one per counted
                    delivery of the other source, `record_source`
                    `import_route_export` or `import_site_counts`. Rows at a
                    supplier's counter are pickups, not drop-offs
    Labels          none

Every stop sits on a route in one inactive hub, under one inactive driver, so no
dispatch, payroll or console path picks it up and no driver's name is stored -
`collected_by` says "driver 1" to "driver n" in order of first appearance.
**Every imported record grades reference**: no handoff point is captured, no
item is weighed, there is no order to carry a delivery profile, and nothing is
labelled. The workbook's own "bronze at best" for the second source becomes
reachable only once `DE-4` captures those handoff points.

**Times are the export's taps**, in New Jersey's clock. Over half the stop times
are ten seconds or less - a driver tapping a batch, not dwell - so they are good
for when deliveries happen in the day and not for how long they take. Nothing
reads dwell from these stops: dwell is computed through orders, and they have
none.

Running it twice is refused: the import hub's existence means it has run.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.identity import resolve_location  # noqa: E402
from app.identity.site_facts import set_site_fact  # noqa: E402
from app.models.driver import Driver  # noqa: E402
from app.models.handoff_point import HandoffPoint  # noqa: E402
from app.models.hub import Hub  # noqa: E402
from app.models.location import Location  # noqa: E402
from app.models.route import Route  # noqa: E402
from app.models.site_address_share import SiteAddressShare  # noqa: E402
from app.models.stop import Stop  # noqa: E402

IMPORT_HUB_NAME = "Experiment 0 import (NJ)"
REGION = "NJ"
NJ_CLOCK = ZoneInfo("America/New_York")
SOURCE_ROUTE_EXPORT = "import_route_export"
SOURCE_SITE_COUNTS = "import_site_counts"
# A site fact's source (`locations.site_fact_sources`).
SOURCE_IMPORT = "import"

# The workbook's kind-of-site guesses, as taxonomy codes. Three are not in
# taxonomy v0.1 and the workbook says so; they are kept as their own codes
# rather than folded into "other", because the taxonomy's rule for a new
# category is ten real examples and two of the three have more. "Unclear from
# name" is unknown, which is null.
KIND_OF_SITE = {
    "Independent repair or tire shop": "independent_repair_or_tire",
    "Body shop": "body_shop",
    "Dealership service or parts counter": "dealership_counter",
    "Parts store or distributor counter": "parts_store_counter",
    "Fleet or transport yard": "fleet_or_transport_yard",
    "Municipal or public fleet garage (not in taxonomy v0.1)": "municipal_fleet_garage",
    "Gas station with service bays (not in taxonomy v0.1)": "gas_station_with_bays",
    "Unclear from name": None,
}

SITES_HEADER = ("Site ID", "Source", "Account no.", "Business name", "Address", "Town", "ZIP")
DELIVERIES_HEADER = ("Date", "Driver", "Manifest", "Account no.", "Business name")


@dataclass
class Site:
    site_id: str
    source: str
    account: str | None
    business: str | None
    address: str | None
    kind_label: str | None
    is_pickup: bool
    shares_with: list[str]
    deliveries_in_data: int


@dataclass
class Delivery:
    date: str
    driver: str
    manifest: str | None
    account: str
    pieces: int
    weight: float
    arrived: str | None
    delivered: str | None


@dataclass
class Workbook:
    sites: list[Site]
    deliveries: list[Delivery]


@dataclass
class Report:
    sites: int = 0
    dropoff_sites: int = 0
    pickup_sites: int = 0
    dropoff_sites_with_address: int = 0
    locations: int = 0
    address_shares_recorded: int = 0
    delivery_rows: int = 0
    delivery_rows_weighed: int = 0
    counted_deliveries: int = 0
    stops: Counter = field(default_factory=Counter)
    unparsed_times: int = 0
    tiers: Counter = field(default_factory=Counter)


def _sheet_with(workbook, header: tuple[str, ...]):
    """Find a tab by its header, not its name: the names carry a source's name,
    and this repository is public."""
    for sheet in workbook.worksheets:
        first = next(sheet.iter_rows(max_row=1, values_only=True), ())
        if tuple(first[: len(header)]) == header:
            return sheet
    raise SystemExit(f"no tab whose header starts {header!r} - is this the Experiment 0 workbook?")


def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_workbook(path: Path) -> Workbook:
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    site_rows = list(_sheet_with(book, SITES_HEADER).iter_rows(values_only=True))
    header = list(site_rows[0])
    col = {name: header.index(name) for name in header if name}
    kind_col = next(i for i, name in enumerate(header) if name and name.startswith("Kind of site"))

    sites = []
    for row in site_rows[1:]:
        if not _text(row[col["Site ID"]]):
            continue
        kind_label = _text(row[kind_col])
        if kind_label not in KIND_OF_SITE and kind_label is not None:
            raise SystemExit(f"unknown kind of site {kind_label!r}: add it to KIND_OF_SITE")
        street, town, zip_code = (_text(row[col[c]]) for c in ("Address", "Town", "ZIP"))
        address = ", ".join(p for p in (street, town, f"{REGION} {zip_code}" if zip_code else REGION) if p) if street else None
        shares = _text(row[col["Shares address with"]])
        sites.append(
            Site(
                site_id=_text(row[col["Site ID"]]),
                source=_text(row[col["Source"]]) or "",
                account=_text(row[col["Account no."]]),
                business=_text(row[col["Business name"]]),
                address=address,
                kind_label=kind_label,
                is_pickup=not (_text(row[col["Pickup or drop-off"]]) or "").startswith("Drop"),
                shares_with=[s.strip() for s in shares.split(";")] if shares else [],
                deliveries_in_data=_int(row[col["Deliveries in data"]]),
            )
        )

    delivery_rows = list(_sheet_with(book, DELIVERIES_HEADER).iter_rows(values_only=True))
    header = list(delivery_rows[0])
    col = {name: header.index(name) for name in header if name}
    deliveries = [
        Delivery(
            date=_text(row[col["Date"]]),
            driver=_text(row[col["Driver"]]) or "",
            manifest=_text(row[col["Manifest"]]),
            account=_text(row[col["Account no."]]) or "",
            pieces=_int(row[col["Pieces"]]),
            weight=float(row[col["Weight"]] or 0),
            arrived=_text(row[col["Arrived"]]),
            delivered=_text(row[col["Delivered"]]),
        )
        for row in delivery_rows[1:]
        if _text(row[col["Date"]])
    ]
    return Workbook(sites=sites, deliveries=deliveries)


def _at(day: str, clock: str | None) -> datetime | None:
    if not clock:
        return None
    try:
        return datetime.strptime(f"{day} {clock}", "%b %d %Y %I:%M %p").replace(tzinfo=NJ_CLOCK)
    except ValueError:
        return None


async def import_workbook(session: AsyncSession, workbook: Workbook) -> Report:
    """Write the workbook as records and return the counts. Flushes; the caller
    commits or rolls back."""
    if await session.scalar(select(Hub.id).where(Hub.name == IMPORT_HUB_NAME)) is not None:
        raise SystemExit(f"{IMPORT_HUB_NAME!r} exists: Experiment 0 has already been imported")

    report = Report()
    sites = workbook.sites
    report.sites = len(sites)
    report.pickup_sites = sum(s.is_pickup for s in sites)
    report.dropoff_sites = report.sites - report.pickup_sites
    report.dropoff_sites_with_address = sum(1 for s in sites if not s.is_pickup and s.address)

    # Not a place: the container the records need, because a stop needs a route
    # and a route a hub. Inactive, so nothing that runs the business reads it,
    # and at no coordinates because the workbook names no depot.
    hub = Hub(
        id=uuid.uuid4(), name=IMPORT_HUB_NAME, timezone=str(NJ_CLOCK), lat=0.0, lng=0.0,
        active=False,
    )
    session.add(hub)
    await session.flush()
    driver = Driver(
        hub_id=hub.id, name="Experiment 0 import", phone="import:experiment-0",
        is_active=False,
    )
    session.add(driver)
    await session.flush()

    # --- Sites ---------------------------------------------------------------
    point_for_site: dict[str, uuid.UUID] = {}
    location_for_site: dict[str, Location] = {}
    points: dict[uuid.UUID, HandoffPoint] = {}
    for site in sites:
        if not site.address:
            continue
        try:
            location = await resolve_location(session, address=site.address)
        except ValueError:
            # An address that names no place: the site's records stay without one.
            continue
        location_for_site[site.site_id] = location
        location.region = location.region or REGION
        # Each fact with its own source (`set_site_fact`): a shared-address
        # answer the desk filled first is the import's once the distributor's
        # own accounts say it, and must not stay marked `desk`.
        if location.kind_of_site is None and KIND_OF_SITE.get(site.kind_label):
            set_site_fact(location, "kind_of_site", KIND_OF_SITE[site.kind_label], SOURCE_IMPORT)
        if site.shares_with:
            set_site_fact(location, "shares_address", True, SOURCE_IMPORT)
        location.site_source = location.site_source or SOURCE_IMPORT
        if location.id not in points:
            points[location.id] = HandoffPoint(id=uuid.uuid4(), location_id=location.id, source="import")
            session.add(points[location.id])
        point_for_site[site.site_id] = points[location.id].id
    await session.flush()
    report.locations = len(points)

    # Two businesses at one address usually resolve to one location, and then
    # there is no pair to record - only `shares_address`. A pair is recorded
    # only where the two addresses were written differently enough to stay apart.
    by_business = {s.business: s for s in sites if s.business}
    pairs = set()
    for site in sites:
        here = location_for_site.get(site.site_id)
        for name in site.shares_with:
            other = location_for_site.get(by_business[name].site_id) if name in by_business else None
            if here is not None and other is not None and other.id != here.id:
                pairs.add((here.id, other.id))
    for here_id, other_id in sorted(pairs):
        session.add(SiteAddressShare(location_id=here_id, shares_with_location_id=other_id))
    report.address_shares_recorded = len(pairs)

    # --- Deliveries with rows ------------------------------------------------
    site_by_account = {s.account: s for s in sites if s.account}
    driver_number: dict[str, int] = {}
    routes: dict[tuple, Route] = {}
    sequence: Counter = Counter()
    report.delivery_rows = len(workbook.deliveries)
    report.delivery_rows_weighed = sum(1 for d in workbook.deliveries if d.weight > 0)
    for delivery in workbook.deliveries:
        site = site_by_account.get(delivery.account)
        number = driver_number.setdefault(delivery.driver, len(driver_number) + 1)
        key = (delivery.manifest or f"{delivery.date} / driver {number}",)
        if key not in routes:
            routes[key] = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
            session.add(routes[key])
            await session.flush()
        sequence[key] += 1
        arrived = _at(delivery.date, delivery.arrived)
        completed = _at(delivery.date, delivery.delivered)
        if arrived is None or completed is None:
            report.unparsed_times += 1
        stop_type = "pickup" if site is not None and site.is_pickup else "dropoff"
        session.add(
            Stop(
                route_id=routes[key].id, stop_type=stop_type, sequence=sequence[key],
                status="completed", parcel_count=delivery.pieces,
                arrived_at=arrived, completed_at=completed,
                handoff_point_id=point_for_site.get(site.site_id) if site else None,
                record_source=SOURCE_ROUTE_EXPORT, collected_by=f"driver {number}",
            )
        )
        report.stops[(SOURCE_ROUTE_EXPORT, stop_type)] += 1

    # --- Deliveries that are only a count per site ---------------------------
    accounts_with_rows = {d.account for d in workbook.deliveries}
    counted = [s for s in sites if s.deliveries_in_data and s.account not in accounts_with_rows]
    if counted:
        ride_along = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
        session.add(ride_along)
        await session.flush()
        position = 0
        for site in counted:
            for _ in range(site.deliveries_in_data):
                position += 1
                stop_type = "pickup" if site.is_pickup else "dropoff"
                session.add(
                    Stop(
                        route_id=ride_along.id, stop_type=stop_type, sequence=position,
                        status="completed", handoff_point_id=point_for_site.get(site.site_id),
                        record_source=SOURCE_SITE_COUNTS, collected_by="ride-along",
                    )
                )
                report.stops[(SOURCE_SITE_COUNTS, stop_type)] += 1
                report.counted_deliveries += 1
    await session.flush()

    tiers = await session.execute(
        text(
            "SELECT tier, count(*) FROM record_tiers rt JOIN stops s ON s.id = rt.stop_id "
            "JOIN routes r ON r.id = s.route_id WHERE r.hub_id = :hub GROUP BY tier"
        ),
        {"hub": hub.id},
    )
    report.tiers = Counter(dict(tiers.all()))
    return report


def _print(report: Report, committed: bool) -> None:
    dropoffs = sum(n for (_, kind), n in report.stops.items() if kind == "dropoff")
    pickups = sum(n for (_, kind), n in report.stops.items() if kind == "pickup")
    print(f"sites                          {report.sites}  ({report.dropoff_sites} drop-off, {report.pickup_sites} pickup)")
    print(f"  drop-off sites with address  {report.dropoff_sites_with_address}")
    print(f"  locations                    {report.locations}  (businesses at one address share one)")
    print(f"  address pairs recorded       {report.address_shares_recorded}")
    print(f"delivery rows                  {report.delivery_rows}  ({report.delivery_rows_weighed} weighed)")
    print(f"  of which at a supplier       {report.stops[(SOURCE_ROUTE_EXPORT, 'pickup')]}  (imported as pickups)")
    print(f"  times that would not parse   {report.unparsed_times}")
    print(f"deliveries counted per site    {report.counted_deliveries}")
    print(f"stops                          {dropoffs} drop-off, {pickups} pickup")
    print(f"records by tier                {dict(report.tiers) or '-'}")
    print("committed" if committed else "dry run: rolled back; --commit to keep")


async def _run(path: Path, commit: bool) -> int:
    workbook = parse_workbook(path)
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            report = await import_workbook(session, workbook)
            if commit:
                await session.commit()
            else:
                await session.rollback()
    finally:
        await engine.dispose()
    _print(report, commit)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--commit", action="store_true", help="keep what was written")
    args = parser.parse_args()
    return asyncio.run(_run(args.workbook, args.commit))


if __name__ == "__main__":
    raise SystemExit(main())
