"""The checklist on a sample, run twice: as measured, and with a written assumption sheet (`DE-17`; the Proof Map's O1).

The Proof Map's rule is *precise, not accurate*: run real records through the
same steps the model will run, fill a missing fact with a labelled assumption,
and show the workings rather than defend the percentage. So the same sample is
scored twice by the checklist (`DE-10`):

    as measured     only what the record holds. On records with no captured
                    door and no weighed item this decides almost nothing, and
                    its missing facts are the collection list
    as assumed      every null the sheet has a rule for is filled, and each
                    filled fact remembers which assumption filled it

and each assumption is then taken out in turn, to count the verdicts that fall
back to can't tell without it. That ranking is the point: it says which fact,
once measured, moves the answer most, so it is what to collect first.

**A measured fact always wins.** An assumption fills only a null, and the first
matching rule in the sheet fills it. **Everything the second run produces is
illustrative** (D-SIM) and is never reported as model accuracy: the sheet is
somebody's guess, and the report prints every rule beside the figures.

## The sheet

    {"version": "a0", "note": "...",
     "assumptions": [
       {"id": "I1", "where": {"pieces": [1, 1]},
        "sets": {"item.weight_kg": 2.0, "item.size_class": "small"},
        "source": "who said so, or that nobody has"}]}

`where` matches a record's own fields: a `[low, high]` range (`high` null for
open-ended) or a value or list of values; an empty `where` matches every record.
`sets` names facts as `part.field`, in the record shape the checklist reads
(`item`, `handoff`, `site`). Standard library.
"""
from __future__ import annotations

import copy
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

from ml.analytics.illustrative import STAMP
from ml.modality.checklist import CANT_TELL, NO, YES, score_record

PARTS = ("item", "handoff", "site")


def _matches(record: Mapping, where: Mapping) -> bool:
    for key, want in where.items():
        have = record.get(key)
        if isinstance(want, list) and len(want) == 2 and all(w is None or isinstance(w, (int, float)) for w in want):
            low, high = want
            if have is None or have < low or (high is not None and have > high):
                return False
        elif isinstance(want, list):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


def check_sheet(sheet: Mapping) -> list[str]:
    """Every problem with a sheet, so it can be refused whole."""
    problems = []
    seen = set()
    for i, rule in enumerate(sheet.get("assumptions", [])):
        rid = rule.get("id")
        if not rid:
            problems.append(f"assumption {i} has no id")
        elif rid in seen:
            problems.append(f"assumption id {rid!r} is used twice")
        seen.add(rid)
        if not rule.get("source"):
            problems.append(f"assumption {rid or i} has no source: say who set it, or that nobody has")
        for fact in rule.get("sets", {}):
            part, _, name = fact.partition(".")
            if part not in PARTS or not name:
                problems.append(f"assumption {rid or i} sets {fact!r}; facts are item.*, handoff.* or site.*")
    if not sheet.get("version"):
        problems.append("the sheet has no version")
    return problems


def fill(record: Mapping, sheet: Mapping, without: str | None = None) -> tuple[dict, dict[str, str]]:
    """A copy of the record with every null the sheet covers filled, and which
    assumption filled each fact. `without` leaves one assumption out."""
    filled = copy.deepcopy(dict(record))
    used: dict[str, str] = {}
    for rule in sheet["assumptions"]:
        if rule["id"] == without or not _matches(record, rule.get("where", {})):
            continue
        for fact, value in rule["sets"].items():
            part, _, name = fact.partition(".")
            holder = filled.setdefault(part, {})
            if holder.get(name) is None and fact not in used:
                holder[name] = copy.deepcopy(value)
                used[fact] = rule["id"]
    return filled, used


def draw_sample(records: Sequence[Mapping], size: int, seed: int, stratum: str = "kind_of_site") -> list[Mapping]:
    """A seeded sample of `size`, allocated across the stratum's values in
    proportion (largest remainder), so a rare kind of site still appears."""
    if size >= len(records):
        return list(records)
    groups: dict[object, list[Mapping]] = defaultdict(list)
    for record in records:
        groups[record.get(stratum)].append(record)
    keys = sorted(groups, key=lambda k: (k is None, str(k)))
    quotas = {k: size * len(groups[k]) / len(records) for k in keys}
    counts = {k: int(q) for k, q in quotas.items()}
    for k in sorted(keys, key=lambda k: quotas[k] - counts[k], reverse=True)[: size - sum(counts.values())]:
        counts[k] += 1
    rng = random.Random(seed)
    sample = []
    for k in keys:
        sample.extend(rng.sample(groups[k], counts[k]))
    return sample


def _tally(verdicts: list) -> dict:
    counts = Counter(v.verdict for v in verdicts)
    n = len(verdicts)
    return {
        "records": n,
        "yes": counts[YES],
        "no": counts[NO],
        "cant_tell": counts[CANT_TELL],
        "yes_share": counts[YES] / n if n else None,
        "coverage": (n - counts[CANT_TELL]) / n if n else None,
        "reasons": dict(Counter(r for v in verdicts for r in v.reasons).most_common()),
        "missing": dict(Counter(m for v in verdicts for m in v.missing).most_common()),
    }


def run(
    records: Sequence[Mapping],
    limits_document: Mapping,
    sheet: Mapping,
    zone_machines: Mapping[str, str | None] | None = None,
) -> dict:
    """Score the records as measured and as assumed, and rank the assumptions.

    `zone_machines` maps a zone's name to its machine; a record's `zones` field
    lists the zones it falls in, and each zone reports the records its machine
    could take under the sheet - the eligible drop-offs inside it (the Proof
    Map's O3, on a sample)."""
    problems = check_sheet(sheet)
    if problems:
        raise ValueError("; ".join(problems))
    machines = list(limits_document["machines"])
    measured = [score_record(r, limits_document) for r in records]
    filled = [fill(r, sheet) for r in records]
    assumed = [score_record(f, limits_document) for f, _ in filled]

    rule_ids = [rule["id"] for rule in sheet["assumptions"]]
    ranking = []
    for rid in rule_ids:
        fell_back = Counter()
        records_using = sum(1 for _, used in filled if rid in used.values())
        if records_using:
            for record, before in zip(records, assumed):
                after = score_record(fill(record, sheet, without=rid)[0], limits_document)
                for m in machines:
                    if before[m].verdict != CANT_TELL and after[m].verdict == CANT_TELL:
                        fell_back[m] += 1
        rule = next(r for r in sheet["assumptions"] if r["id"] == rid)
        ranking.append({
            "id": rid,
            "sets": rule["sets"],
            "where": rule.get("where", {}),
            "source": rule["source"],
            "records_filled": records_using,
            "falls_back_by_machine": {m: fell_back[m] for m in machines},
            "verdicts_resting_on_it": sum(fell_back.values()),
        })
    ranking.sort(key=lambda r: (-r["verdicts_resting_on_it"], r["id"]))

    zones = []
    for name, machine in (zone_machines or {}).items():
        inside = [i for i, r in enumerate(records) if name in (r.get("zones") or [])]
        row = {"zone": name, "machine": machine, "records_inside": len(inside)}
        if machine in machines:
            row["eligible_as_assumed"] = sum(assumed[i][machine].verdict == YES for i in inside)
            row["eligible_as_measured"] = sum(measured[i][machine].verdict == YES for i in inside)
        zones.append(row)

    return {
        "stamp": STAMP,
        "limits_version": limits_document["version"],
        "sheet_version": sheet["version"],
        "sheet_note": sheet.get("note", ""),
        "records": len(records),
        "measured": {m: _tally([v[m] for v in measured]) for m in machines},
        "assumed": {m: _tally([v[m] for v in assumed]) for m in machines},
        "assumptions": ranking,
        "zones": zones,
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def render_markdown(report: Mapping, *, title: str, about: str) -> str:
    """The report as Markdown: the two runs, the collection list, the ranking, the zones."""
    lines = [
        f"# {title}",
        "",
        f"**{report['stamp']}.** {about} Machine limits {report['limits_version']}, "
        f"assumption sheet {report['sheet_version']}.",
    ]
    if report["sheet_note"]:
        lines += ["", f"The sheet says: {report['sheet_note']}"]
    lines += [
        "",
        f"## Eligible share by machine ({report['records']} drop-offs)",
        "",
        "| Machine | As measured: yes / no / can't tell | As assumed: yes / no / can't tell | Yes, as assumed | Top reason for no |",
        "|---|---|---|---|---|",
    ]
    for machine, m in report["measured"].items():
        a = report["assumed"][machine]
        reason = next(iter(a["reasons"]), "-")
        lines.append(
            f"| {machine} | {m['yes']} / {m['no']} / {m['cant_tell']} | {a['yes']} / {a['no']} / {a['cant_tell']} "
            f"| {_pct(a['yes_share'])} | {reason} |"
        )
    lines += [
        "",
        "As measured is what the records hold. As assumed fills each null the sheet covers; every figure in "
        "that column rests on the rules below.",
        "",
        "## What to collect: the missing facts, as measured",
        "",
        "Drop-offs each missing fact leaves at can't tell, per machine. A fact a machine does not need is blank.",
        "",
        "| Missing fact | " + " | ".join(report["measured"]) + " |",
        "|---|" + "---|" * len(report["measured"]),
    ]
    facts = list(dict.fromkeys(f for m in report["measured"].values() for f in m["missing"]))
    for fact in facts:
        cells = [str(m["missing"].get(fact, "")) for m in report["measured"].values()]
        lines.append(f"| {fact} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Which assumption the answer rests on",
        "",
        "Each rule taken out in turn: the verdicts (yes or no, any machine) that fall back to can't tell without it. "
        "The top rows are the facts to measure first.",
        "",
        "| Rule | Sets | Where | Drop-offs filled | Verdicts resting on it | Source |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["assumptions"]:
        sets = ", ".join(f"{k} = {v}" for k, v in r["sets"].items())
        where = ", ".join(f"{k} {v}" for k, v in r["where"].items()) or "every drop-off"
        lines.append(
            f"| {r['id']} | {sets} | {where} | {r['records_filled']} | {r['verdicts_resting_on_it']} | {r['source']} |"
        )
    if report["zones"]:
        lines += [
            "",
            "## Eligible drop-offs inside each operator zone",
            "",
            "| Zone | Machine | Drop-offs inside | Eligible, as assumed | Eligible, as measured |",
            "|---|---|---|---|---|",
        ]
        for z in report["zones"]:
            lines.append(
                f"| {z['zone']} | {z['machine'] or '-'} | {z['records_inside']} | "
                f"{z.get('eligible_as_assumed', '-')} | {z.get('eligible_as_measured', '-')} |"
            )
    return "\n".join(lines) + "\n"
