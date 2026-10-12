#!/usr/bin/env python3
"""Run the checklist baseline (`DE-10`) over exported records. Standard library.

    python scripts/export_golden_records.py   --out ml/out/records.jsonl
    python scripts/machine_limits.py export    --out ml/out/machine_limits.json
    python scripts/freeze_test_sites.py export --out ml/out/test_sites.json
    python scripts/run_checklist_baseline.py   --fallback-cost 35 --out ml/out/checklist_baseline

Writes `<out>.json` and `<out>.md`: per machine and group, the checklist's yes,
no and can't tell, its reasons for no, and the missing facts behind every can't
tell - on the frozen test sites and on the rest, apart - and `<out>.scorecard.*`,
`DE-11`'s scorecard of those verdicts against the labels. `--fallback-cost` is
what one wrong yes costs to put right, in dollars: $35 by decision D-COST,
assumed rather than measured. The test-site file is optional only while nothing has been drawn;
without it every record is training, and the report says there are no test sites.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.modality.baseline import render_markdown, run  # noqa: E402
from ml.modality.checklist import score_record  # noqa: E402
from ml.modality.scorecard import render_markdown as render_scorecard  # noqa: E402
from ml.modality.scorecard import scorecard  # noqa: E402
from ml.modality.splits import load_test_sites  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records", type=Path, default=Path("ml/out/records.jsonl"))
    parser.add_argument("--limits", type=Path, default=Path("ml/out/machine_limits.json"))
    parser.add_argument("--test-sites", type=Path, default=Path("ml/out/test_sites.json"))
    parser.add_argument("--out", type=Path, default=Path("ml/out/checklist_baseline"))
    # D-COST (ROADMAP_1.5.md §2.5, 12 Oct 2026): about 45 minutes of a human
    # re-drive at $45/hr loaded. Assumed; the scorecard prints it beside the figure.
    parser.add_argument("--fallback-cost", type=float, default=35.0, help="dollars per wrong yes; assumed (D-COST)")
    args = parser.parse_args()

    records = [json.loads(line) for line in args.records.read_text().splitlines() if line.strip()]
    limits = json.loads(args.limits.read_text())
    test_sites = load_test_sites(args.test_sites) if args.test_sites.exists() else frozenset()

    report = run(records, limits, test_sites)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    args.out.with_suffix(".md").write_text(render_markdown(report))
    predictions = {
        r["stop_id"]: {m: v.verdict for m, v in score_record(r, limits).items()} for r in records
    }
    card = scorecard(records, predictions, test_sites, fallback_cost=args.fallback_cost)
    args.out.with_name(args.out.name + ".scorecard.json").write_text(json.dumps(card, indent=2) + "\n")
    args.out.with_name(args.out.name + ".scorecard.md").write_text(render_scorecard(card))
    print(render_markdown(report))
    print(render_scorecard(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
