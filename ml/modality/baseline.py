"""Run the checklist over a set of records and say what it found (`DE-10`).

Per machine and group (kind of site x setting): how many records the checklist
called yes, no and can't tell, why it said no, and which missing facts stopped
it - the collection list. Test sites and training sites are reported apart
(`ml/modality/splits.py`), because the checklist is the bar a model is judged
against on the test sites, and only there.

How often a verdict was *right* needs labels, and is `DE-11`'s scorecard. This
says what the rules can decide at all, which on records with no captured
handoff point is very little - and saying so, with the list of what to collect,
is the point of running it before the re-drive.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

from ml.modality.checklist import CANT_TELL, NO, YES, score_record
from ml.modality.splits import split_on_test_sites

UNGROUPED = "(kind or setting unknown)"


def _group(record: Mapping) -> str:
    kind, setting = (record.get("group") or [None, None])
    return f"{kind} / {setting}" if kind and setting else UNGROUPED


def _summarise(records: list[Mapping], limits_document: Mapping) -> dict:
    cells: dict = defaultdict(lambda: {"records": 0, "verdicts": Counter(), "reasons": Counter(), "missing": Counter()})
    for record in records:
        for machine, verdict in score_record(record, limits_document).items():
            cell = cells[(machine, _group(record))]
            cell["records"] += 1
            cell["verdicts"][verdict.verdict] += 1
            cell["reasons"].update(verdict.reasons)
            cell["missing"].update(verdict.missing)
    out = []
    for (machine, group), cell in sorted(cells.items()):
        n = cell["records"]
        decided = n - cell["verdicts"][CANT_TELL]
        out.append({
            "machine": machine,
            "group": group,
            "records": n,
            "yes": cell["verdicts"][YES],
            "no": cell["verdicts"][NO],
            "cant_tell": cell["verdicts"][CANT_TELL],
            "coverage": decided / n if n else None,
            "reasons": dict(cell["reasons"].most_common()),
            "missing": dict(cell["missing"].most_common()),
        })
    return {"records": len(records), "cells": out}


def run(records: Iterable[Mapping], limits_document: Mapping, test_sites: frozenset[str]) -> dict:
    """The checklist over every record, split on the frozen test sites."""
    training, test = split_on_test_sites(list(records), test_sites)
    return {
        "limits_version": limits_document["version"],
        "test_sites": len(test_sites),
        "test": _summarise(test, limits_document),
        "training": _summarise(training, limits_document),
    }


def render_markdown(report: Mapping) -> str:
    lines = [
        f"# Checklist baseline - machine limits {report['limits_version']}",
        "",
        f"{report['test_sites']} frozen test sites. Coverage is the share the checklist could decide "
        "(yes or no); the rest are can't tell, and the missing facts say why.",
    ]
    for side in ("test", "training"):
        part = report[side]
        lines += ["", f"## {side.capitalize()} ({part['records']} records)", ""]
        if not part["cells"]:
            lines.append("No records.")
            continue
        lines.append("| Machine | Group | Records | Yes | No | Can't tell | Coverage | Top reason for no | Top missing fact |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for c in part["cells"]:
            coverage = f"{c['coverage']:.0%}" if c["coverage"] is not None else "-"
            reason = next(iter(c["reasons"]), "-")
            missing = next(iter(c["missing"]), "-")
            lines.append(
                f"| {c['machine']} | {c['group']} | {c['records']} | {c['yes']} | {c['no']} | "
                f"{c['cant_tell']} | {coverage} | {reason} | {missing} |"
            )
    return "\n".join(lines) + "\n"
