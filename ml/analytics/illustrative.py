"""The illustrative analytics pack (`DE-16`, Rich, 5 Oct 2026).

Three readings of Experiment 0 for the back pocket: when in the day deliveries
happen, what share of drop-offs fall within a drone's radius of a hub, and how
far drop-offs are from it. **Every chart is stamped illustrative** (D-SIM): the
data is one distributor's three months in New Jersey, the times are the export's
taps, and nothing here is model accuracy or may be quoted as such.

Standard library. The SVGs are single-series bars in the house green, with a
tooltip on every bar and the stamp in the title, so a chart lifted out of the
pack still carries it.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import datetime
from html import escape

STAMP = "ILLUSTRATIVE - not model accuracy (D-SIM)"
BRAND_GREEN = "#0A6644"
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
EARTH_RADIUS_KM = 6371.0088


def hour_of_day_counts(times: Iterable[datetime]) -> list[int]:
    counts = [0] * 24
    for moment in times:
        counts[moment.hour] += 1
    return counts


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def within_radius(points: Sequence[tuple[float, float]], hub: tuple[float, float], radius_km: float) -> int:
    """How many points lie within `radius_km` of the hub, edge included."""
    return sum(haversine_km(hub, p) <= radius_km for p in points)


def distance_bins(
    points: Sequence[tuple[float, float]], hub: tuple[float, float], width_km: float, bins: int
) -> list[int]:
    """Counts in `bins` bands of `width_km` from the hub; the last band takes
    everything beyond it."""
    counts = [0] * bins
    for p in points:
        counts[min(int(haversine_km(hub, p) // width_km), bins - 1)] += 1
    return counts


def bar_chart_svg(
    *, title: str, subtitle: str, labels: Sequence[str], values: Sequence[int], unit: str
) -> str:
    """One series, vertical bars from a zero baseline, a recessive grid, a
    tooltip per bar, and the illustrative stamp."""
    width, height = 720, 360
    left, right, top, bottom = 48, 16, 84, 40
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = max(values) if values and max(values) > 0 else 1
    # A round tick step - 1, 2 or 5 times a power of ten - giving at most five.
    magnitude = 10 ** max(0, math.floor(math.log10(peak / 5)) if peak >= 5 else 0)
    tick = next(m * magnitude for m in (1, 2, 5, 10) if peak / (m * magnitude) <= 5)
    ticks = math.ceil(peak / tick)
    ceiling = ticks * tick
    gap = 2
    bar_w = plot_w / len(values)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(title)}. {escape(STAMP)}" font-family="Aptos, Helvetica, Arial, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="{SURFACE}"/>',
        f'<text x="{left}" y="28" font-size="16" font-weight="600" fill="{TEXT_PRIMARY}">{escape(title)}</text>',
        f'<text x="{left}" y="48" font-size="12" fill="{TEXT_SECONDARY}">{escape(subtitle)}</text>',
        f'<text x="{left}" y="66" font-size="11" font-weight="600" fill="{TEXT_SECONDARY}" '
        f'letter-spacing="0.06em">{escape(STAMP)}</text>',
    ]
    for i in range(ticks + 1):
        value = tick * i
        y = top + plot_h - plot_h * value / ceiling
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(
            f'<text x="{left - 6}" y="{y + 4:.1f}" font-size="11" text-anchor="end" fill="{TEXT_SECONDARY}">{value:g}</text>'
        )
    label_every = max(1, math.ceil(len(labels) / 12))
    for i, (label, value) in enumerate(zip(labels, values)):
        x = left + i * bar_w + gap / 2
        h = plot_h * value / ceiling
        y = top + plot_h - h
        radius = min(4, (bar_w - gap) / 2, h)
        # Rounded at the data end, square at the baseline.
        path = (
            f"M{x:.1f},{top + plot_h:.1f} V{y + radius:.1f} Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f} "
            f"H{x + bar_w - gap - radius:.1f} Q{x + bar_w - gap:.1f},{y:.1f} {x + bar_w - gap:.1f},{y + radius:.1f} "
            f"V{top + plot_h:.1f} Z"
        ) if h > 0 else ""
        parts.append(
            f'<g><title>{escape(label)}: {value} {escape(unit)}</title>'
            f'<rect x="{x:.1f}" y="{top}" width="{bar_w - gap:.1f}" height="{plot_h}" fill="transparent"/>'
            + (f'<path d="{path}" fill="{BRAND_GREEN}"/>' if path else "")
            + "</g>"
        )
        if i % label_every == 0:
            parts.append(
                f'<text x="{x + (bar_w - gap) / 2:.1f}" y="{top + plot_h + 16}" font-size="11" '
                f'text-anchor="middle" fill="{TEXT_SECONDARY}">{escape(label)}</text>'
            )
    parts.append(
        f'<line x1="{left}" x2="{width - right}" y1="{top + plot_h}" y2="{top + plot_h}" stroke="{TEXT_SECONDARY}" stroke-width="1"/>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
