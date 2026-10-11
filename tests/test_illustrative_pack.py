"""DE-16: the illustrative pack's arithmetic and its stamp, on invented points."""
from datetime import datetime

import pytest

from ml.analytics.illustrative import (
    STAMP,
    bar_chart_svg,
    distance_bins,
    haversine_km,
    hour_of_day_counts,
    within_radius,
)

HUB = (40.0, -74.0)


def test_hours_are_counted_by_the_hour_they_start():
    counts = hour_of_day_counts([datetime(2026, 1, 2, 9, 59), datetime(2026, 1, 2, 10, 0), datetime(2026, 1, 3, 10, 30)])

    assert (counts[9], counts[10], sum(counts)) == (1, 2, 3)


def test_a_degree_of_latitude_is_about_111_km():
    assert haversine_km(HUB, (41.0, -74.0)) == pytest.approx(111.2, abs=0.2)


def test_the_radius_and_the_last_band_takes_the_rest():
    points = [HUB, (40.0 + 4.999 / 111.195, -74.0), (40.5, -74.0)]

    assert within_radius(points, HUB, 5.0) == 2
    assert distance_bins(points, HUB, 2.0, 3) == [1, 0, 2]


def test_every_chart_carries_the_stamp_and_a_tooltip_per_bar():
    svg = bar_chart_svg(title="T", subtitle="S", labels=["a", "b", "c"], values=[0, 3, 7], unit="drop-offs")

    assert STAMP in svg
    assert svg.count("<title>") == 3
    assert "b: 3 drop-offs" in svg
