#!/usr/bin/env python3
"""Read the machine limits, and record a new version when an operator confirms one.

    python scripts/machine_limits.py list
    python scripts/machine_limits.py show   [--version v1]
    python scripts/machine_limits.py export [--version v1] [--out limits.json]
    python scripts/machine_limits.py add    --version v2 --file limits.json --by rich

`DE-2`. Every machine verdict names the limits it was scored against, and the
seed version `v1` is assumed throughout - the drone payload alone is 2.5 kg in
one document and about 9 lb in another. When an operator answers, the answer has
to get in without SQL against production, and without editing `v1`: labels
already scored under it must keep meaning what they meant.

**The round trip is `export`, edit, `add`.** `export` writes a version in exactly
the shape `add` reads, so a new version starts as a copy of the last with the
confirmed limits changed and their `basis` set to `operator_reported`. `add`
refuses a file that leaves any machine or any limit out, and names every problem
at once. The checklist baseline in `ml/` (`DE-10`) reads the same file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import AsyncSessionLocal  # noqa: E402
from app.record.machine_limits import (  # noqa: E402
    LIMIT_FIELDS,
    MachineLimitsError,
    add_machine_limits_version,
    limits_document,
    machine_limits,
    machine_limits_versions,
)


async def _list() -> int:
    async with AsyncSessionLocal() as session:
        versions = await machine_limits_versions(session)
    if not versions:
        print("no machine limits recorded - migration 0073 seeds v1")
        return 1
    print(f"{'version':<12} {'recorded':<17} drone / sidewalk_robot / car")
    for version, at, bases in versions:
        print(f"{version:<12} {at:%Y-%m-%d %H:%M}  {' / '.join(bases)}")
    print("\nThe last one listed is what `show` and `export` use when no --version is given.")
    return 0


async def _document(version: str | None) -> dict:
    async with AsyncSessionLocal() as session:
        limits = await machine_limits(session, version)
    if not limits:
        raise SystemExit(f"no machine limits version {version!r}" if version else "no machine limits recorded")
    return limits_document(limits)


async def _show(version: str | None) -> int:
    document = await _document(version)
    print(f"machine limits {document['version']}")
    for machine, limits in document["machines"].items():
        print(f"\n  {machine}")
        for field in LIMIT_FIELDS:
            if field != "source":
                print(f"    {field:<27} {limits[field]}")
        print(f"    source  {limits['source']}")
    return 0


async def _export(version: str | None, out: str | None) -> int:
    text = json.dumps(await _document(version), indent=2) + "\n"
    if out:
        Path(out).write_text(text)
        print(f"wrote {out}")
    else:
        sys.stdout.write(text)
    return 0


async def _add(version: str, path: str, by: str) -> int:
    document = json.loads(Path(path).read_text())
    machines = document.get("machines") if isinstance(document, dict) else None
    if not isinstance(machines, dict):
        raise SystemExit(f"{path} has no 'machines' object - start from `export` and edit that")
    async with AsyncSessionLocal() as session:
        try:
            await add_machine_limits_version(session, version=version, machines=machines, recorded_by=by)
        except MachineLimitsError as problem:
            raise SystemExit(str(problem)) from problem
        await session.commit()
    print(
        f"recorded machine limits {version}. Labels scored from now on should cite it; "
        "labels already scored keep the version they cite."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("--version")
    export = sub.add_parser("export")
    export.add_argument("--version")
    export.add_argument("--out", help="a file to write; stdout if left out")
    add = sub.add_parser("add")
    add.add_argument("--version", required=True, help="a new name; versions are never edited")
    add.add_argument("--file", required=True, help="JSON in the shape `export` writes")
    add.add_argument("--by", required=True, help="who is recording it")

    args = parser.parse_args()
    if args.command == "list":
        return asyncio.run(_list())
    if args.command == "show":
        return asyncio.run(_show(args.version))
    if args.command == "export":
        return asyncio.run(_export(args.version, args.out))
    return asyncio.run(_add(args.version, args.file, args.by))


if __name__ == "__main__":
    raise SystemExit(main())
