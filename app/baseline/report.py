"""
Rendering the baseline so that the caveats are as hard to skip as the numbers.

A scorecard is read by someone deciding whether to cut a live customer over from a
working scaffold to this system. The failure this module guards against is a clean
table of figures that looks authoritative and omits the part where a third of the rows
did not parse.

So: rejected rows are printed before the metrics, not in an appendix. A metric that
could not be computed keeps its row and states why, rather than disappearing. Warnings
are repeated at the foot of the report where a reader who skimmed the table still meets
them.

The pilot comparators (docs/ROADMAP.md section G) are printed beside the money figures
and explicitly labelled as the *gig* control group. They come from a different demand
path - a solo driver taking platform offers, not a distributor's own fleet running
scheduled routes - and the two are not like for like. Printing them unlabelled beside a
distributor's cost per stop would invite exactly the comparison the label forbids.
"""
from __future__ import annotations

import json
from collections import Counter

from app.baseline.loaders import LoadResult
from app.baseline.metrics import Baseline, Metric

RULE = "=" * 78
THIN = "-" * 78


def _format_value(metric: Metric) -> str:
    """The number with its unit attached, so a column of figures stays unambiguous."""
    if metric.value is None:
        return "n/a"
    if metric.unit == "$":
        return f"${metric.value:,.2f}"
    if metric.unit.startswith("$/"):
        return f"${metric.value:,.2f}/{metric.unit[2:]}"
    if metric.unit == "%":
        return f"{metric.value:.2f}%"
    if metric.unit in ("drops", "stops"):
        return f"{metric.value:,.0f}"
    number = f"{metric.value:,.3f}".rstrip("0").rstrip(".")
    return f"{number} {metric.unit}" if metric.unit else number


def _rejected_block(result: LoadResult | None, label: str) -> list[str]:
    if result is None:
        return []
    lines = [f"{label}: {result.usable:,} usable of {result.total_data_rows:,} data rows"]
    lines.append(f"  file: {result.path}")
    if result.issues:
        reasons = Counter(issue.reason for issue in result.issues)
        lines.append(f"  REJECTED {len(result.issues):,} row(s):")
        for reason, count in reasons.most_common(8):
            examples = [str(i.line) for i in result.issues if i.reason == reason][:4]
            lines.append(f"    {count:>6,}  {reason}  (line{'s' if len(examples) > 1 else ''} {', '.join(examples)}…)")
        if len(reasons) > 8:
            lines.append(f"    … and {len(reasons) - 8} further reason(s)")
    return lines


def _coverage_block(result: LoadResult | None, label: str) -> list[str]:
    if result is None or not result.rows:
        return []
    lines = [f"{label} field coverage:"]
    for field_name in sorted(result.column_map.mapping):
        fraction = result.coverage_of(field_name)
        if fraction is None:
            continue
        flag = "" if fraction > 0.99 else ("   <-- partial" if fraction > 0 else "   <-- column mapped but empty")
        lines.append(f"  {field_name:<14} {fraction * 100:6.1f}%{flag}")
    unmapped = [f for f in result.column_map.mapping if not result.column_map.has(f)]
    if unmapped:
        lines.append(f"  unmapped: {', '.join(sorted(unmapped))}")
    return lines


def render_text(baseline: Baseline) -> str:
    """The human-facing report."""
    out: list[str] = [RULE, "OPERATIONAL BASELINE  (control group for the W9 shadow-mode scorecard)", RULE, ""]

    out.append("SOURCE DATA")
    out.append(THIN)
    for result, label in ((baseline.activity, "Activity"), (baseline.invoices, "Invoices")):
        block = _rejected_block(result, label)
        if block:
            out.extend(block)
            out.append("")

    for result, label in ((baseline.activity, "Activity"), (baseline.invoices, "Invoice")):
        block = _coverage_block(result, label)
        if block:
            out.extend(block)
            out.append("")

    out.append("BASELINE METRICS")
    out.append(THIN)
    for metric in baseline.metrics:
        value = _format_value(metric)
        out.append(f"{metric.name:<26} {value:>16}")
        if metric.value is None:
            out.append(f"{'':<26}   not computable: {metric.basis}")
            continue
        if metric.basis:
            out.append(f"{'':<26}   from {metric.basis}")
        if metric.comparator:
            name, target = metric.comparator
            delta = metric.value - target
            direction = "above" if delta > 0 else "below"
            out.append(
                f"{'':<26}   vs {name} ${target:,.2f} - {abs(delta):,.2f} {direction} "
                "(different demand path; orientation only, not a like-for-like target)"
            )
        if metric.caveat:
            out.append(f"{'':<26}   ! {metric.caveat}")
    out.append("")

    unavailable = [m for m in baseline.metrics if not m.available]
    if unavailable:
        out.append("NOT COMPUTABLE FROM THESE EXPORTS")
        out.append(THIN)
        for metric in unavailable:
            out.append(f"  {metric.name}: {metric.basis}")
        out.append("")

    out.append("SCORECARD METRICS THIS TOOL CANNOT PRODUCE")
    out.append(THIN)
    out.append("  re-plan speed, decision divergence, hold-release integrity, human touches")
    out.append("  - these measure a live system's behaviour, not a historical record.")
    out.append("  They come from app/shadow/ once shadow mode runs, not from these files.")
    out.append("")

    if baseline.warnings:
        out.append("WARNINGS")
        out.append(THIN)
        for warning in baseline.warnings:
            out.append(f"  ! {warning}")
        out.append("")

    out.append(RULE)
    return "\n".join(out)


def render_json(baseline: Baseline) -> str:
    """Machine-readable, for pinning a baseline next to a later scorecard run."""
    payload = {
        "metrics": {
            m.name: {
                "value": m.value,
                "unit": m.unit,
                "basis": m.basis,
                "contributing_rows": m.contributing_rows,
                "caveat": m.caveat or None,
                "comparator": (
                    {"name": m.comparator[0], "value": m.comparator[1]} if m.comparator else None
                ),
            }
            for m in baseline.metrics
        },
        "warnings": baseline.warnings,
        "sources": {
            label: {
                "path": result.path,
                "data_rows": result.total_data_rows,
                "usable_rows": result.usable,
                "rejected_rows": len(result.issues),
                "column_map": result.column_map.mapping,
            }
            for label, result in (("activity", baseline.activity), ("invoices", baseline.invoices))
            if result is not None
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)
