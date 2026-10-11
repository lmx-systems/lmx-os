"""The modality scorecard (`DE-11`): how often a "yes" was right, and what it cost.

Runs on any predictor's verdicts - the checklist baseline (`DE-10`) today, a
model (`DE-12`) later - against the labels, per machine and group:

    yes accuracy        of the records the predictor called yes, the share the
                        first scorer also called yes. A scorer's can't tell is
                        not a yes: sending a machine where a person could not
                        tell is a wrong yes until shown otherwise
    lower bound         one-sided 95% Clopper-Pearson. The design's arithmetic:
                        149 clean records for 98% - a run with no mistakes still
                        only proves what its size allows
    coverage            the share the predictor decided (yes or no)
    lift                yes accuracy and coverage against the baseline's, on
                        the same records; none when the predictor is the baseline
    wrong-yes cost      wrong yeses x the fallback cost of one, per order routed
                        to the machine. The fallback cost is assumed and has no
                        default; it is printed beside the figure
    agreement           first scorer against the blind second, where both scored;
                        a label set under 85% is too subjective to train on (DE-9)

**Scored only where D-TEST allows**: records at frozen test sites, graded gold,
with a first scorer's verdict for the machine. Everything else is counted and
reported as excluded, so a small denominator is visible rather than flattering.

**No pass or fail.** The bars are D-ACC - 90% with a person checking every yes,
95% for pilots, 98% for the licence - and D-ACC is still open, so the scorecard
prints them for reading against and decides nothing. Accuracy against record
count (the learning curve) comes from a trainer, which the checklist is not; it
is carried when a trainer supplies it.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping

from ml.modality.baseline import UNGROUPED
from ml.modality.checklist import CANT_TELL, YES

PROPOSED_BARS = {"person checks every yes": 0.90, "pilots": 0.95, "licence": 0.98}
AGREEMENT_GATE = 0.85


def _log_pmf(k: int, n: int, p: float) -> float:
    if p <= 0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1:
        return 0.0 if k == n else -math.inf
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * math.log(p) + (n - k) * math.log(1 - p)


def _upper_tail(x: int, n: int, p: float) -> float:
    return sum(math.exp(_log_pmf(k, n, p)) for k in range(x, n + 1))


def clopper_pearson_lower(successes: int, n: int, confidence: float = 0.95) -> float | None:
    """The one-sided lower bound: the smallest true rate under which a result
    this good or better would happen at least (1 - confidence) of the time."""
    if n == 0:
        return None
    if successes == 0:
        return 0.0
    alpha = 1 - confidence
    lo, hi = 0.0, successes / n
    for _ in range(100):
        mid = (lo + hi) / 2
        if _upper_tail(successes, n, mid) < alpha:
            lo = mid
        else:
            hi = mid
    return lo


def _group(record: Mapping) -> str:
    kind, setting = (record.get("group") or [None, None])
    return f"{kind} / {setting}" if kind and setting else UNGROUPED


def _first(record: Mapping, machine: str, blind: bool) -> str | None:
    for label in record.get("labels") or []:
        if label["machine"] == machine and bool(label["is_blind_second"]) == blind:
            return label["verdict"]
    return None


def _accuracy(pairs: list[tuple[str, str]]) -> dict:
    called_yes = [truth for predicted, truth in pairs if predicted == YES]
    right = sum(truth == YES for truth in called_yes)
    decided = sum(predicted != CANT_TELL for predicted, _ in pairs)
    return {
        "scored": len(pairs),
        "called_yes": len(called_yes),
        "right_yes": right,
        "wrong_yes": len(called_yes) - right,
        "yes_accuracy": right / len(called_yes) if called_yes else None,
        "yes_accuracy_lower_95": clopper_pearson_lower(right, len(called_yes)),
        "coverage": decided / len(pairs) if pairs else None,
    }


def scorecard(
    records: Iterable[Mapping],
    predictions: Mapping[str, Mapping[str, str]],
    test_sites: frozenset[str],
    *,
    fallback_cost: float,
    baseline: Mapping[str, Mapping[str, str]] | None = None,
    learning_curve: list[dict] | None = None,
) -> dict:
    """`predictions` and `baseline` map stop_id -> machine -> verdict. Pass the
    baseline's own verdicts as `predictions` and no `baseline` to score the
    checklist itself."""
    if fallback_cost < 0:
        raise ValueError("fallback_cost is a cost per wrong yes, zero or more")

    records = list(records)
    machines = sorted({m for verdicts in predictions.values() for m in verdicts})
    cells: dict = defaultdict(lambda: {"pairs": [], "baseline_pairs": [], "agreement": [0, 0]})
    excluded = {"not_a_test_site": 0, "not_gold": 0, "no_first_label": 0}

    for record in records:
        group = _group(record)
        for machine in machines:
            first, second = _first(record, machine, False), _first(record, machine, True)
            if first is not None and second is not None:
                cell = cells[(machine, group)]
                cell["agreement"][0] += first == second
                cell["agreement"][1] += 1
        if record.get("location_id") not in test_sites:
            excluded["not_a_test_site"] += 1
            continue
        if record.get("tier") != "gold":
            excluded["not_gold"] += 1
            continue
        for machine in machines:
            truth = _first(record, machine, False)
            if truth is None:
                excluded["no_first_label"] += 1
                continue
            cell = cells[(machine, group)]
            cell["pairs"].append((predictions[record["stop_id"]][machine], truth))
            if baseline is not None:
                cell["baseline_pairs"].append((baseline[record["stop_id"]][machine], truth))

    out = []
    for (machine, group), cell in sorted(cells.items()):
        result = {"machine": machine, "group": group, **_accuracy(cell["pairs"])}
        routed = result["called_yes"]
        result["wrong_yes_cost_per_routed_order"] = (
            result["wrong_yes"] * fallback_cost / routed if routed else None
        )
        if baseline is not None:
            base = _accuracy(cell["baseline_pairs"])
            result["lift"] = {
                "yes_accuracy": (
                    result["yes_accuracy"] - base["yes_accuracy"]
                    if result["yes_accuracy"] is not None and base["yes_accuracy"] is not None else None
                ),
                "coverage": (
                    result["coverage"] - base["coverage"]
                    if result["coverage"] is not None and base["coverage"] is not None else None
                ),
            }
        agreed, both = cell["agreement"]
        result["agreement"] = {
            "both_scored": both,
            "rate": agreed / both if both else None,
            "under_gate": (agreed / both < AGREEMENT_GATE) if both else None,
        }
        out.append(result)

    return {
        "fallback_cost_assumed": fallback_cost,
        "bars_proposed_d_acc_open": PROPOSED_BARS,
        "excluded": excluded,
        "learning_curve": learning_curve or [],
        "cells": out,
    }


def render_markdown(card: Mapping) -> str:
    def pct(value):
        return f"{value:.1%}" if value is not None else "-"

    bars = ", ".join(f"{name} {bar:.0%}" for name, bar in card["bars_proposed_d_acc_open"].items())
    lines = [
        "# Modality scorecard",
        "",
        f"Scored on frozen gold test sites only. Excluded: {card['excluded']}.",
        f"Bars proposed under D-ACC, which is open, so nothing here passes or fails: {bars}.",
        f"Wrong-yes cost uses an assumed fallback cost of ${card['fallback_cost_assumed']:.2f} per wrong yes.",
        "",
    ]
    if not card["cells"]:
        return "\n".join(lines + ["Nothing to score: no labelled gold records at a test site yet."]) + "\n"
    lines.append("| Machine | Group | Scored | Yes calls | Yes accuracy | 95% lower | Coverage | Lift (acc / cov) | Wrong-yes $/routed | Agreement |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in card["cells"]:
        lift = c.get("lift")
        lift_text = f"{pct(lift['yes_accuracy'])} / {pct(lift['coverage'])}" if lift else "baseline"
        cost = c["wrong_yes_cost_per_routed_order"]
        agreement = c["agreement"]
        agreement_text = (
            f"{pct(agreement['rate'])} of {agreement['both_scored']}"
            + (" (under 85%)" if agreement["under_gate"] else "")
            if agreement["both_scored"] else "-"
        )
        lines.append(
            f"| {c['machine']} | {c['group']} | {c['scored']} | {c['called_yes']} | {pct(c['yes_accuracy'])} | "
            f"{pct(c['yes_accuracy_lower_95'])} | {pct(c['coverage'])} | {lift_text} | "
            f"{'-' if cost is None else f'${cost:.2f}'} | {agreement_text} |"
        )
    if card["learning_curve"]:
        lines += ["", "## Accuracy against record count", "", "| Training records | Yes accuracy |", "|---|---|"]
        lines += [f"| {p['records']} | {pct(p['yes_accuracy'])} |" for p in card["learning_curve"]]
    return "\n".join(lines) + "\n"
