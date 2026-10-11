#!/usr/bin/env python3
"""Write every golden record as one JSON line, for the modality harness in `ml/`.

    python scripts/export_golden_records.py --out ml/out/records.jsonl

`ml/` shares no code with `app/`, so the checklist baseline (`DE-10`) and the
scorecard (`DE-11`) read files: this one, the machine limits
(`scripts/machine_limits.py export`) and the frozen test sites
(`scripts/freeze_test_sites.py export`).

One line per record - a drop-off or visit stop, graded by the `record_tiers`
view - with its tier and source, its site (the canonical location, after
merges), its group, the item, the drop-off handoff point's facts, who receives,
and every machine label on it. **No names, addresses or customer fields**: the
file is safe to keep beside the harness, and the harness has no use for them.
Unknown is null, as it is in the record.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import AsyncSessionLocal  # noqa: E402

_RECORDS = text(
    """
    SELECT rt.stop_id, rt.tier, rt.record_source, rt.recorded_at, rt.stop_type,
           hp.location_id,
           hp.stop_legal, hp.walk_distance_m, hp.steps_or_ramps, hp.continuous_sidewalk,
           hp.obstructions, hp.drone_open_ground, hp.overhead,
           o.item_weight_kg, o.item_size_class, o.item_hazmat_or_liquid
    FROM record_tiers rt
    LEFT JOIN handoff_points hp ON hp.id = rt.handoff_point_id
    LEFT JOIN orders o ON o.id = rt.order_id
    ORDER BY rt.recorded_at, rt.stop_id
    """
)
_LOCATIONS = text(
    """
    SELECT l.id, l.merged_into_id, l.region, l.kind_of_site, l.setting,
           rp.receiving_hours, rp.who_receives
    FROM locations l LEFT JOIN receiver_profiles rp ON rp.location_id = l.id
    """
)
_LABELS = text(
    "SELECT stop_id, machine, verdict, reason_codes, scorer_id, is_blind_second, machine_limits_version "
    "FROM machine_labels ORDER BY created_at"
)


def _canonical(location_id, merged_into: dict) -> object:
    seen = 0
    while location_id is not None and merged_into.get(location_id) is not None and seen < 16:
        location_id, seen = merged_into[location_id], seen + 1
    return location_id


async def _export(out: Path) -> int:
    async with AsyncSessionLocal() as session:
        records = (await session.execute(_RECORDS)).mappings().all()
        locations = {row["id"]: row for row in (await session.execute(_LOCATIONS)).mappings().all()}
        labels: dict = {}
        for row in (await session.execute(_LABELS)).mappings().all():
            labels.setdefault(row["stop_id"], []).append(row)

    merged_into = {lid: row["merged_into_id"] for lid, row in locations.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as handle:
        for r in records:
            site_id = _canonical(r["location_id"], merged_into)
            site = locations.get(site_id) or {}
            weight = r["item_weight_kg"]
            line = {
                "stop_id": str(r["stop_id"]),
                "tier": r["tier"],
                "record_source": r["record_source"],
                "recorded_at": r["recorded_at"].isoformat() if r["recorded_at"] else None,
                "stop_type": r["stop_type"],
                "location_id": str(site_id) if site_id else None,
                "region": site.get("region"),
                "group": [site.get("kind_of_site"), site.get("setting")],
                "item": {
                    "weight_kg": float(weight) if weight is not None else None,
                    "size_class": r["item_size_class"],
                    "hazmat_or_liquid": r["item_hazmat_or_liquid"],
                },
                "handoff": {
                    key: r[key]
                    for key in (
                        "stop_legal", "walk_distance_m", "steps_or_ramps", "continuous_sidewalk",
                        "obstructions", "drone_open_ground", "overhead",
                    )
                },
                "site": {
                    "receiving_hours": site.get("receiving_hours"),
                    "who_receives": site.get("who_receives"),
                },
                "labels": [
                    {
                        "machine": label["machine"],
                        "verdict": label["verdict"],
                        "reason_codes": label["reason_codes"],
                        "scorer_id": label["scorer_id"],
                        "is_blind_second": label["is_blind_second"],
                        "machine_limits_version": label["machine_limits_version"],
                    }
                    for label in labels.get(r["stop_id"], [])
                ],
            }
            handle.write(json.dumps(line, default=str) + "\n")
    print(f"wrote {len(records)} records to {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    return asyncio.run(_export(parser.parse_args().out))


if __name__ == "__main__":
    raise SystemExit(main())
