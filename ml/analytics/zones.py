"""Operator zones over drop-offs, for the partner mock-up (`DE-17`; the Proof Map's E6 and O3).

Which drop-offs fall inside an autonomy operator's service area, and when in the
day. The Proof Map's first step is to move one distributor's real drop-offs to
a city where operators fly and drive (Round Rock), lay a drone zone and a
road-AV zone over them, and count. **Everything here is illustrative** (D-SIM):
the move keeps the footprint's shape and distances but not its streets, and the
zones are operators' published areas as we read them, not polygons they gave us.

Zones arrive as GeoJSON: a Polygon or MultiPolygon feature, or a Point with a
`radius_km` property for a circle (Zipline publishes its Round Rock area as a
radius). Each feature's `name` names the zone; `machine` says which machine in
the machine limits it is for (`drone`, `sidewalk_robot` or `car`), so the
eligibility run can count eligible drop-offs inside it.

Standard library, as the rest of `ml/analytics`. Coordinates are (lat, lng)
throughout; GeoJSON's (lng, lat) is turned round at the edge. Containment is a
flat ray cast, which is exact enough at a city's scale.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from html import escape

from ml.analytics.illustrative import (
    BRAND_GREEN,
    EARTH_RADIUS_KM,
    GRID,
    STAMP,
    SURFACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

Point = tuple[float, float]
Ring = list[Point]
CIRCLE_VERTICES = 72
# One colour per zone, after the house green; outlines only, so drop-offs show through.
ZONE_COLOURS = ("#2f5fd0", "#a8330a", "#8a5300", "#5b3fa0")


@dataclass
class Zone:
    name: str
    machine: str | None
    # Each polygon is its outer ring then any holes.
    polygons: list[list[Ring]]
    note: str = ""
    centre: Point | None = None
    radius_km: float | None = None


@dataclass
class ZoneCount:
    zone: str
    machine: str | None
    inside: int
    share: float
    by_hour: list[int] = field(default_factory=lambda: [0] * 24)


def circle(centre: Point, radius_km: float, vertices: int = CIRCLE_VERTICES) -> Ring:
    """A ring of `vertices` points `radius_km` from the centre."""
    ring = []
    for i in range(vertices):
        bearing = 2 * math.pi * i / vertices
        north, east = radius_km * math.cos(bearing), radius_km * math.sin(bearing)
        ring.append(offset(centre, north, east))
    ring.append(ring[0])
    return ring


def offset(origin: Point, north_km: float, east_km: float) -> Point:
    """The point `north_km` and `east_km` from the origin (equirectangular)."""
    lat0, lng0 = origin
    lat = lat0 + math.degrees(north_km / EARTH_RADIUS_KM)
    lng = lng0 + math.degrees(east_km / (EARTH_RADIUS_KM * math.cos(math.radians(lat0))))
    return lat, lng


def displacement(origin: Point, point: Point) -> tuple[float, float]:
    """(north_km, east_km) from the origin to the point; the inverse of `offset`."""
    north = math.radians(point[0] - origin[0]) * EARTH_RADIUS_KM
    east = math.radians(point[1] - origin[1]) * EARTH_RADIUS_KM * math.cos(math.radians(origin[0]))
    return north, east


def relocate(points: Sequence[Point], origin: Point, target: Point) -> list[Point]:
    """Each point moved so the origin lands on the target, keeping its distance
    and bearing from the origin: the same footprint, in another city."""
    return [offset(target, *displacement(origin, p)) for p in points]


def centroid(points: Sequence[Point]) -> Point:
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _ring(coordinates: Sequence[Sequence[float]]) -> Ring:
    return [(float(lat), float(lng)) for lng, lat, *_ in coordinates]


def load_zones(document: Mapping) -> list[Zone]:
    """Zones from a GeoJSON FeatureCollection; refuses a feature it cannot read."""
    zones = []
    for feature in document.get("features", []):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        kind, coords = geometry.get("type"), geometry.get("coordinates")
        name = props.get("name")
        if not name:
            raise ValueError("every zone needs a name property")
        zone = Zone(name=name, machine=props.get("machine"), polygons=[], note=props.get("note", ""))
        if kind == "Point":
            radius = props.get("radius_km")
            if not radius:
                raise ValueError(f"zone {name!r} is a point with no radius_km")
            zone.centre, zone.radius_km = (float(coords[1]), float(coords[0])), float(radius)
            zone.polygons = [[circle(zone.centre, zone.radius_km)]]
        elif kind == "Polygon":
            zone.polygons = [[_ring(r) for r in coords]]
        elif kind == "MultiPolygon":
            zone.polygons = [[_ring(r) for r in polygon] for polygon in coords]
        else:
            raise ValueError(f"zone {name!r} has geometry {kind!r}; use Polygon, MultiPolygon or Point")
        zones.append(zone)
    return zones


def _in_ring(point: Point, ring: Ring) -> bool:
    lat, lng = point
    inside = False
    for (lat1, lng1), (lat2, lng2) in zip(ring, ring[1:] + ring[:1]):
        if (lat1 > lat) != (lat2 > lat):
            crossing = lng1 + (lat - lat1) * (lng2 - lng1) / (lat2 - lat1)
            if lng < crossing:
                inside = not inside
    return inside


def contains(zone: Zone, point: Point) -> bool:
    """Inside an outer ring and outside its holes, in any of the zone's polygons."""
    return any(_in_ring(point, rings[0]) and not any(_in_ring(point, h) for h in rings[1:]) for rings in zone.polygons)


def count_in_zones(drops: Sequence[tuple[Point, int | None]], zones: Iterable[Zone]) -> list[ZoneCount]:
    """Per zone: how many drop-offs fall inside, their share of all, and by hour.
    `drops` is (point, hour of day or None)."""
    out = []
    for zone in zones:
        count = ZoneCount(zone=zone.name, machine=zone.machine, inside=0, share=0.0)
        for point, hour in drops:
            if contains(zone, point):
                count.inside += 1
                if hour is not None:
                    count.by_hour[hour] += 1
        count.share = count.inside / len(drops) if drops else 0.0
        out.append(count)
    return out


def map_svg(*, title: str, subtitle: str, points: Sequence[Point], zones: Sequence[Zone], pad_km: float = 2.0) -> str:
    """Drop-offs as dots, sized by how many share a place, and zones as outlines on
    a plain ground, north up, with a 5 km scale bar and the illustrative stamp. No
    basemap: no streets to read."""
    width, height = 720, 560
    left, right, top, bottom = 24, 24, 96, 48
    every = list(points) + [p for z in zones for rings in z.polygons for p in rings[0]]
    origin = centroid(every)
    xy = [displacement(origin, p) for p in every]
    north_min = min(n for n, _ in xy) - pad_km
    north_max = max(n for n, _ in xy) + pad_km
    east_min = min(e for _, e in xy) - pad_km
    east_max = max(e for _, e in xy) + pad_km
    plot_w, plot_h = width - left - right, height - top - bottom
    scale = min(plot_w / (east_max - east_min), plot_h / (north_max - north_min))
    # Centred in the plot, whichever way the extent is long.
    x0 = left + (plot_w - (east_max - east_min) * scale) / 2
    y0 = top + (plot_h - (north_max - north_min) * scale) / 2

    def project(p: Point) -> tuple[float, float]:
        north, east = displacement(origin, p)
        return x0 + (east - east_min) * scale, y0 + (north_max - north) * scale

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(title)}. {escape(STAMP)}" font-family="Aptos, Helvetica, Arial, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="{SURFACE}"/>',
        f'<text x="{left}" y="28" font-size="16" font-weight="600" fill="{TEXT_PRIMARY}">{escape(title)}</text>',
        f'<text x="{left}" y="48" font-size="12" fill="{TEXT_SECONDARY}">{escape(subtitle)}</text>',
        f'<text x="{left}" y="66" font-size="11" font-weight="600" fill="{TEXT_SECONDARY}" '
        f'letter-spacing="0.06em">{escape(STAMP)}</text>',
    ]
    # One dot per place, its area in proportion to the drop-offs there.
    for (lat, lng), n in sorted(Counter((round(p[0], 6), round(p[1], 6)) for p in points).items()):
        x, y = project((lat, lng))
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{2 + 1.1 * math.sqrt(n):.1f}" fill="{BRAND_GREEN}" '
            f'fill-opacity="0.45"><title>{n} drop-offs</title></circle>'
        )
    legend_y = 84
    for i, zone in enumerate(zones):
        colour = ZONE_COLOURS[i % len(ZONE_COLOURS)]
        for rings in zone.polygons:
            d = " ".join(
                "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in (project(p) for p in ring)) + " Z" for ring in rings
            )
            parts.append(
                f'<path d="{d}" fill="{colour}" fill-opacity="0.06" stroke="{colour}" stroke-width="2" '
                f'fill-rule="evenodd"><title>{escape(zone.name)}</title></path>'
            )
        parts.append(f'<rect x="{width - 230}" y="{legend_y - 9 + i * 18}" width="12" height="3" fill="{colour}"/>')
        parts.append(
            f'<text x="{width - 212}" y="{legend_y - 4 + i * 18}" font-size="11" fill="{TEXT_SECONDARY}">'
            f"{escape(zone.name)}</text>"
        )
    bar = 5 * scale
    y = height - 20
    parts.append(f'<line x1="{left}" x2="{left + bar:.1f}" y1="{y}" y2="{y}" stroke="{TEXT_SECONDARY}" stroke-width="2"/>')
    parts.append(f'<text x="{left + bar + 6:.1f}" y="{y + 4}" font-size="11" fill="{TEXT_SECONDARY}">5 km</text>')
    parts.append(
        f'<line x1="{left}" x2="{width - right}" y1="{top - 8}" y2="{top - 8}" stroke="{GRID}" stroke-width="1"/>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def drops_geojson(points: Sequence[Point], hours: Sequence[int | None]) -> dict:
    """The drop-offs as GeoJSON points (hour only, no site or name), for a map tool."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lng, 6), round(lat, 6)]},
                "properties": {"hour": hour, "note": STAMP},
            }
            for (lat, lng), hour in zip(points, hours)
        ],
    }
