"""Desk pre-fill: a site's kind, setting and shared-address answer from open data (`DE-15`).

`docs/ROADMAP_1.5.md` DE-15 and §2.5 D-DESK. Before a technician stands at a
site, public data already says something about it: what businesses are listed
at its address, and how built-up the land around it is. This fills the three
site facts a record's end needs besides its door (`record_tiers`, migration
0077) - kind of site, setting, and whether the address is shared - so a visit
or a gig delivery is not held back by facts nobody was ever going to capture at
the door.

**Nothing about a site leaves the machine (D-DESK, provisional).** Every source
is a bulk file for the whole state, downloaded once and matched here
(`scripts/desk_prefill.py fetch`): Overture's places and address points by the
state's bounding box, and the 2020 Census blocks. No address and no coordinate
of ours is sent anywhere, which is why this needs no answer yet on sending
addresses to public services. That answer, and whether an agent is worth adding
over this, are Sourabh's.

**A desk fact is a guess, and is graded as one.** It is written only where the
field is empty - never over the import's, a technician's or anyone's - with its
source `desk` in `locations.site_fact_sources`, and a record whose end counts
only thanks to one is graded bronze at most (migration 0080). Unknown stays
null: an address public data does not know gets nothing.

The rules, all assumptions to be checked against the first technician visits:

    kind of site     the listed businesses at the site's street address (house
                     number, street and ZIP; a suite number is ignored), each
                     mapped from its Overture category to the taxonomy. One
                     kind, or none: two businesses of different kinds at one
                     address leave it unknown, since which one we deliver to is
                     exactly what the desk cannot see
    shares address   two or more open, listed businesses at that street
                     address: yes (two listings of one category within 30 m
                     are one business listed twice). Otherwise unknown, never
                     no: one listing does not show one tenant, and on
                     Experiment 0 it was wrong at six of the seven shared
                     sites it could be checked against. A listing in another
                     ZIP counts when it stands within 300 m of the site's
                     address point
    setting          housing units within 1 km of the site, per square mile:
                     2,213 or more is a city core, below that a suburb, and
                     below 102 is rural, which the vocabulary has no word for,
                     so it stays unknown. The thresholds are Jed Kolko's
                     urban/suburban/rural households-per-square-mile cut-offs
                     (2015), applied to Census housing units around the site
                     rather than households across a ZIP

Every function here is pure except `apply_desk_facts`; the script does the
reading of files and the database.
"""
from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.site_facts import SOURCE_DESK, set_site_fact
from app.models.location import SITE_FACTS, Location

DESK = SOURCE_DESK
FACTS = SITE_FACTS

# --- Setting -----------------------------------------------------------------
CITY_CORE_UNITS_PER_SQ_MI = 2213
RURAL_BELOW_UNITS_PER_SQ_MI = 102
SETTING_RADIUS_M = 1000.0
# A place listed in another ZIP still counts as at the site within this distance.
SAME_STREET_M = 300.0
# Two listings of one category this close are one business listed twice.
DUPLICATE_M = 30.0
_SQ_M_PER_SQ_MI = 2_589_988.11

# --- Places ------------------------------------------------------------------
# Below this Overture's own confidence that the place exists, it is not counted.
MIN_PLACE_CONFIDENCE = 0.5
_CLOSED = {"permanently_closed", "temporarily_closed", "closed"}
# Listed things at an address that are not a business somebody receives at.
_NOT_A_TENANT = {"atm", "ev_charging_station", "parking", "parking_lot", "bus_stop", "post_box", "landmark_and_historical_building"}

# Overture category to taxonomy code (`docs/DATA_DICTIONARY.md`). The
# categories are Overture's places taxonomy (release 2026-09); anything not
# here maps to nothing, which is unknown, never "other".
_REPAIR = {
    "automotive_repair", "automotive_service", "tire_dealer_and_repair", "tire_shop",
    "brake_service_and_repair", "transmission_repair", "engine_repair_service",
    "auto_electrical_repair", "exhaust_and_muffler_repair", "wheel_and_rim_repair",
    "oil_change_station", "truck_repair", "trailer_repair", "hybrid_car_repair",
}
KIND_FROM_CATEGORY: dict[str, str] = {
    **{c: "independent_repair_or_tire" for c in _REPAIR},
    "auto_body_shop": "body_shop",
    # New-vehicle dealers run a service and parts counter; a used-car lot or a
    # broker usually does not, so those stay unknown.
    "auto_dealer": "dealership_counter",
    "truck_dealer": "dealership_counter",
    "commercial_vehicle_dealer": "dealership_counter",
    "auto_parts_store": "parts_store_counter",
    "truck_parts_and_accessories": "parts_store_counter",
    "motor_freight_trucking": "fleet_or_transport_yard",
    "truck_rental_service": "fleet_or_transport_yard",
}
# A gas station is one with bays only when it is also listed as a repair shop.
_GAS_STATION = "gas_station"
GAS_STATION_WITH_BAYS = "gas_station_with_bays"


# --- Addresses ---------------------------------------------------------------
_SUFFIX = {
    "street": "st", "str": "st", "avenue": "ave", "av": "ave", "road": "rd", "boulevard": "blvd",
    "drive": "dr", "lane": "ln", "place": "pl", "court": "ct", "parkway": "pkwy", "pky": "pkwy",
    "terrace": "ter", "turnpike": "tpke", "tpk": "tpke", "circle": "cir", "square": "sq",
    "plaza": "plz", "highway": "hwy", "expressway": "expy", "trail": "trl", "way": "way",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "mount": "mt", "saint": "st", "fort": "ft",
}
_ROUTE = re.compile(
    r"\b(?:(?P<county>county|co)|us|u\s*s|nj|state)?\s*(?:hwy|highway|route|rte|rt|road|rd)?\s*[-#]?\s*(?P<n>\d{1,3})\b"
)
_ROUTE_WORDS = re.compile(r"\b(?:us|u\s*s|nj|state|county|hwy|highway|route|rte|rt)\b")
_DIRECTIONS = {"n", "s", "e", "w", "north", "south", "east", "west"}
_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\s*$")
_NUMBER = re.compile(r"^\s*(\d+[a-z]?)(?:\s*-\s*\d+[a-z]?)?\s+(.+)$")


@dataclass(frozen=True)
class StreetAddress:
    """A street address reduced to what identifies a building: number, street, ZIP."""

    number: str
    street: str
    postcode: str


def normalize_street(street: str) -> str:
    """Case, punctuation and the usual abbreviations folded, so "Route 46" meets "US Highway 46".

    A numbered state or US highway becomes `route N` whatever it was called, and
    a county road `cr N` - a county route 9 and US 9 are different roads that
    can share a house number and a ZIP. Words before the route that are not a
    direction stay ("Old Route 22" is not Route 22). Otherwise each word maps
    to its USPS-style abbreviation. Deliberately no fuzzier than that: a near
    miss leaves the site unmatched, which is unknown, while a fuzzy match puts
    another business's facts on our site.
    """
    s = street.casefold().replace(".", " ").replace(",", " ")
    s = re.sub(r"[^a-z0-9#\- ]", " ", s)
    s = " ".join(s.split())
    if _ROUTE_WORDS.search(s):
        m = _ROUTE.search(s)
        if m:
            head = " ".join(_SUFFIX.get(w, w) for w in s[: m.start()].split() if w not in _DIRECTIONS)
            tail = " ".join(_SUFFIX.get(w, w) for w in s[m.end():].split() if w not in _DIRECTIONS)
            road = f"{'cr' if m.group('county') else 'route'} {int(m.group('n'))}"
            return " ".join(part for part in (head, road, tail) if part)
    return " ".join(_SUFFIX.get(w, w) for w in s.split())


def parse_street_line(line: str, postcode: str | None) -> StreetAddress | None:
    """"123-125 Main St Ste 4" and a ZIP, as the building's number, street and ZIP; None if it names no building."""
    if not postcode or not line:
        return None
    line = re.split(r"\b(?:ste|suite|unit|apt|bldg|building|fl|floor|#)\b", line.casefold(), maxsplit=1)[0]
    m = _NUMBER.match(line.strip().strip(","))
    if not m:
        return None
    zip5 = postcode.strip()[:5]
    if not zip5.isdigit():
        return None
    street = normalize_street(m.group(2))
    return StreetAddress(number=m.group(1), street=street, postcode=zip5) if street else None


def parse_site_address(address: str) -> StreetAddress | None:
    """A location's address - "street, town, NJ 07601" - as number, street and ZIP."""
    zip_match = _ZIP.search(address.strip())
    if not zip_match:
        return None
    street_line = address.split(",", 1)[0]
    return parse_street_line(street_line, zip_match.group(1))


# --- Evidence ----------------------------------------------------------------
@dataclass(frozen=True)
class Place:
    """One listed place: its category, its other categories, and whether it is open and real."""

    category: str | None
    alternates: tuple[str, ...] = ()
    confidence: float | None = None
    operating_status: str | None = None
    lat: float | None = None
    lng: float | None = None
    postcode: str | None = None

    @property
    def counts(self) -> bool:
        return (
            (self.confidence or 0.0) >= MIN_PLACE_CONFIDENCE
            and (self.operating_status or "open") not in _CLOSED
            and self.category not in _NOT_A_TENANT
        )


def kind_of_place(place: Place) -> str | None:
    if place.category == _GAS_STATION:
        return GAS_STATION_WITH_BAYS if _REPAIR.intersection(place.alternates) else None
    return KIND_FROM_CATEGORY.get(place.category or "")


def kind_from_places(places: Iterable[Place]) -> str | None:
    """The one kind the listed businesses agree on, or None when they disagree or none maps."""
    kinds = {k for p in places if p.counts and (k := kind_of_place(p))}
    return kinds.pop() if len(kinds) == 1 else None


def tenants(places: Iterable[Place]) -> list[Place]:
    """The businesses the listings describe: one per listing, except that two
    listings of the same category within `DUPLICATE_M` of each other are taken
    for one business listed twice - Overture often carries a business once per
    source. Two real businesses of one kind side by side are undercounted by
    this, which errs toward "not shared"; counting every duplicate would call
    most single shops shared."""
    kept: list[Place] = []
    for p in places:
        if not p.counts:
            continue
        if any(
            k.category == p.category
            and None not in (k.lat, k.lng, p.lat, p.lng)
            and _metres(k.lat, k.lng, p.lat, p.lng) <= DUPLICATE_M  # type: ignore[arg-type]
            for k in kept
        ):
            continue
        kept.append(p)
    return kept


def shares_address_from_places(places: Iterable[Place]) -> bool | None:
    """Yes when two or more businesses are listed; otherwise unknown, never no.

    One listing is not evidence of one tenant. Run on Experiment 0, the twelve
    sites the distributor's own accounts show to be shared were listed as one
    business at six and none at five: a "no" from one listing would have been
    wrong at six of the seven it could be checked against.
    """
    return True if len(tenants(places)) >= 2 else None


def housing_units_per_sq_mi(lat: float, lng: float, blocks: Iterable[tuple[float, float, int]]) -> float:
    """Census housing units whose block centre is within `SETTING_RADIUS_M`, per square mile of that circle."""
    dlat = SETTING_RADIUS_M / 111_320.0
    dlng = dlat / max(math.cos(math.radians(lat)), 1e-6)
    units = 0
    for blat, blng, n in blocks:
        if abs(blat - lat) > dlat or abs(blng - lng) > dlng:
            continue
        if _metres(lat, lng, blat, blng) <= SETTING_RADIUS_M:
            units += n
    area_sq_mi = math.pi * SETTING_RADIUS_M**2 / _SQ_M_PER_SQ_MI
    return units / area_sq_mi


def setting_from_density(units_per_sq_mi: float) -> str | None:
    if units_per_sq_mi >= CITY_CORE_UNITS_PER_SQ_MI:
        return "city_core"
    if units_per_sq_mi >= RURAL_BELOW_UNITS_PER_SQ_MI:
        return "suburb"
    return None


def _metres(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a_lat, a_lng, b_lat, b_lng))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


@dataclass
class SiteEvidence:
    """What public data says about one site. `matched` is whether its address was found at all."""

    location_id: uuid.UUID
    matched: bool = False
    kind_of_site: str | None = None
    setting: str | None = None
    shares_address: bool | None = None

    def fact(self, name: str) -> object:
        return getattr(self, name)


def evidence_for_site(
    location_id: uuid.UUID,
    address: StreetAddress | None,
    address_points: Sequence[tuple[float, float]],
    candidates: Sequence[Place],
    blocks: Iterable[tuple[float, float, int]],
) -> SiteEvidence:
    """The three facts for one site.

    `address_points` are the address points at the site's number, street and
    ZIP. `candidates` are the places listed at its number and street in any ZIP:
    a place's listed ZIP is often wrong, so one in another ZIP still counts when
    it stands within `SAME_STREET_M` of the site's address point - the same
    stretch of the same street, not a namesake street in another town. The
    site's position is the middle of its address points, or of its places in
    its own ZIP when the address points do not have it; with neither there is
    no position and no setting.
    """
    evidence = SiteEvidence(location_id=location_id)
    if address is None:
        return evidence
    point = _middle(address_points)
    places = [
        p for p in candidates
        if p.postcode == address.postcode
        or (point is not None and p.lat is not None and p.lng is not None
            and _metres(point[0], point[1], p.lat, p.lng) <= SAME_STREET_M)
    ]
    if point is None:
        point = _middle([(p.lat, p.lng) for p in places if p.postcode == address.postcode and p.lat is not None and p.lng is not None])
    if point is None and not places:
        return evidence
    evidence.matched = True
    evidence.kind_of_site = kind_from_places(places)
    evidence.shares_address = shares_address_from_places(places)
    if point is not None:
        evidence.setting = setting_from_density(housing_units_per_sq_mi(point[0], point[1], blocks))
    return evidence


def _middle(points: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    if not points:
        return None
    return sorted(p[0] for p in points)[len(points) // 2], sorted(p[1] for p in points)[len(points) // 2]


# --- Writing -----------------------------------------------------------------
@dataclass
class PrefillReport:
    """Counts only - never an address."""

    sites: int = 0
    matched: int = 0
    filled: Counter[str] = field(default_factory=Counter)
    already_known: Counter[str] = field(default_factory=Counter)
    agrees: Counter[str] = field(default_factory=Counter)
    disagrees: Counter[str] = field(default_factory=Counter)
    unknown: Counter[str] = field(default_factory=Counter)
    place_known_after: int = 0


async def apply_desk_facts(session: AsyncSession, evidence: Iterable[SiteEvidence]) -> PrefillReport:
    """Write each desk fact where the site has none, marked `desk`; count the rest.

    Each fact goes through `set_site_fact`, so its own `site_fact_sources` entry
    says `desk`; the site's `site_source` is left alone, because the per-fact
    entry is what `record_tiers` reads and a site-wide `desk` would mislabel a
    fact some later writer adds. A fact already known - from the import, a
    technician or a person - is never touched; where the desk would have said something else it is counted as a
    disagreement, the first measure of how good the desk is. The caller commits.
    """
    report = PrefillReport()
    for ev in evidence:
        location = (
            await session.execute(select(Location).where(Location.id == ev.location_id).with_for_update())
        ).scalar_one_or_none()
        if location is None:
            continue
        report.sites += 1
        report.matched += ev.matched
        for name in FACTS:
            desk_value = ev.fact(name)
            current = getattr(location, name)
            if current is not None:
                report.already_known[name] += 1
                if desk_value is not None:
                    if desk_value == current:
                        report.agrees[name] += 1
                    else:
                        report.disagrees[name] += 1
                continue
            if desk_value is None:
                report.unknown[name] += 1
                continue
            set_site_fact(location, name, desk_value, DESK)
            report.filled[name] += 1
        if all(getattr(location, name) is not None for name in FACTS):
            report.place_known_after += 1
    await session.flush()
    return report
