"""DE-17: the partner mock-up's zones: containment, circles, the move, on invented points."""
import json
from pathlib import Path

import pytest

from ml.analytics.illustrative import STAMP, haversine_km
from ml.analytics.zones import (
    centroid,
    contains,
    count_in_zones,
    displacement,
    drops_geojson,
    load_zones,
    map_svg,
    relocate,
)

SQUARE = {
    "type": "Feature",
    "properties": {"name": "Square", "machine": "car"},
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [[-97.80, 30.20], [-97.70, 30.20], [-97.70, 30.30], [-97.80, 30.30], [-97.80, 30.20]],
            # A hole in the middle.
            [[-97.76, 30.24], [-97.74, 30.24], [-97.74, 30.26], [-97.76, 30.26], [-97.76, 30.24]],
        ],
    },
}
CIRCLE = {
    "type": "Feature",
    "properties": {"name": "Circle", "machine": "drone", "radius_km": 5.0},
    "geometry": {"type": "Point", "coordinates": [-97.64, 30.52]},
}


def _zones(*features):
    return load_zones({"type": "FeatureCollection", "features": list(features)})


def test_a_polygon_holds_what_is_inside_it_and_not_its_hole():
    (square,) = _zones(SQUARE)

    assert contains(square, (30.22, -97.78))
    assert not contains(square, (30.25, -97.75))  # in the hole
    assert not contains(square, (30.35, -97.75))  # north of it


def test_a_point_with_a_radius_is_a_circle_of_that_radius():
    (circle,) = _zones(CIRCLE)
    centre = (30.52, -97.64)
    just_in = relocate([(0.0, 0.0)], (0.0, 0.0), centre)[0]

    assert circle.radius_km == 5.0
    assert contains(circle, just_in)
    for vertex in circle.polygons[0][0]:
        assert haversine_km(centre, vertex) == pytest.approx(5.0, abs=0.02)


def test_a_zone_needs_a_name_and_a_shape_it_can_read():
    with pytest.raises(ValueError, match="name"):
        _zones({**CIRCLE, "properties": {"radius_km": 1}})
    with pytest.raises(ValueError, match="radius_km"):
        _zones({**CIRCLE, "properties": {"name": "No radius"}})
    with pytest.raises(ValueError, match="LineString"):
        _zones({**SQUARE, "geometry": {"type": "LineString", "coordinates": []}})


def test_the_move_keeps_every_distance_and_bearing_from_the_centre():
    nj = [(40.90, -74.05), (40.95, -74.00), (40.85, -74.10)]
    origin = centroid(nj)
    target = (30.5086, -97.6788)

    moved = relocate(nj, origin, target)

    assert centroid(moved) == pytest.approx(target, abs=1e-3)
    for before, after in zip(nj, moved):
        assert displacement(target, after) == pytest.approx(displacement(origin, before), abs=0.05)


def test_counts_share_and_hours_per_zone():
    square, circle = _zones(SQUARE, CIRCLE)
    drops = [((30.22, -97.78), 9), ((30.22, -97.78), 10), ((30.52, -97.64), 10), ((30.0, -97.0), None)]

    by_zone = {c.zone: c for c in count_in_zones(drops, [square, circle])}

    assert (by_zone["Square"].inside, by_zone["Square"].share) == (2, 0.5)
    assert by_zone["Square"].by_hour[9] == by_zone["Square"].by_hour[10] == 1
    assert by_zone["Circle"].inside == 1


def test_the_map_carries_the_stamp_and_a_dot_per_place():
    square, circle = _zones(SQUARE, CIRCLE)
    points = [(30.22, -97.78), (30.22, -97.78), (30.52, -97.64)]

    svg = map_svg(title="T", subtitle="S", points=points, zones=[square, circle])

    assert STAMP in svg
    assert svg.count("<circle") == 2
    assert "<title>2 drop-offs</title>" in svg


def test_the_points_file_carries_hours_and_nothing_about_a_site():
    feature = drops_geojson([(30.5, -97.6)], [11])["features"][0]

    assert feature["geometry"]["coordinates"] == [-97.6, 30.5]
    assert set(feature["properties"]) == {"hour", "note"}


def test_the_austin_zones_file_loads():
    path = Path(__file__).resolve().parent.parent / "ml" / "analytics" / "zones_austin.geojson"

    zones = load_zones(json.loads(path.read_text()))

    assert {z.machine for z in zones} == {"drone", "car"}
    assert all(z.note for z in zones)
