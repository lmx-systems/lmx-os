"""Machine limits: add a version, read one, write it out for `ml/`.

`docs/ROADMAP_1.5.md` DE-2. The table and what each limit means are in
`app/models/machine_limit.py`. This is the only way a version gets in after the
seed, so it checks what the database cannot: that a version covers all three
machines, and that every limit is given rather than defaulted. A version missing
the robot's step rule would score every robot verdict against nothing.

`ml/` shares no code with `app/` (the checklist baseline, `DE-10`, runs there),
so `limits_document` is the hand-off: a plain dictionary in the shape `add`
reads back, written to a file by `scripts/machine_limits.py export`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, get_args

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.machine_label import MACHINES
from app.models.machine_limit import BASES, MachineLimit
from app.schemas.lmx_order import SizeClass

SIZE_CLASSES: tuple[str, ...] = get_args(SizeClass)

# Every limit a version must state, per machine. `basis` and `source` say how
# the row is known; none of these may be left to a default.
LIMIT_FIELDS = (
    "max_weight_kg",
    "max_size_class",
    "hazmat_or_liquid_allowed",
    "needs_step_free_path",
    "needs_continuous_sidewalk",
    "needs_open_ground",
    "max_walk_distance_m",
    "basis",
    "source",
)


class MachineLimitsError(ValueError):
    """A proposed version is incomplete or contradicts the table's rules."""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _problems(machine: str, given: dict[str, Any]) -> list[str]:
    problems = []
    missing = [f for f in LIMIT_FIELDS if f not in given]
    extra = sorted(set(given) - set(LIMIT_FIELDS))
    if missing:
        problems.append(f"{machine}: missing {', '.join(missing)}")
    if extra:
        problems.append(f"{machine}: unknown {', '.join(extra)}")
    if missing:
        return problems

    if not _number(given["max_weight_kg"]) or given["max_weight_kg"] <= 0:
        problems.append(f"{machine}: max_weight_kg must be a number above zero")
    if given["max_size_class"] not in SIZE_CLASSES:
        problems.append(f"{machine}: max_size_class must be one of {', '.join(SIZE_CLASSES)}")
    for flag in (
        "hazmat_or_liquid_allowed",
        "needs_step_free_path",
        "needs_continuous_sidewalk",
        "needs_open_ground",
    ):
        if not isinstance(given[flag], bool):
            problems.append(f"{machine}: {flag} must be true or false")
    walk = given["max_walk_distance_m"]
    if walk is not None and (not _number(walk) or walk < 0):
        problems.append(f"{machine}: max_walk_distance_m must be null (no limit) or zero or more")
    if given["basis"] not in BASES:
        problems.append(f"{machine}: basis must be one of {', '.join(BASES)}")
    if not isinstance(given["source"], str) or not given["source"].strip():
        problems.append(f"{machine}: source must say where the figures came from")
    return problems


async def add_machine_limits_version(
    session: AsyncSession,
    *,
    version: str,
    machines: dict[str, dict[str, Any]],
    recorded_by: str,
) -> list[MachineLimit]:
    """Record a new version: one row per machine, all limits stated.

    Raises `MachineLimitsError` naming every problem at once, so a file with
    three mistakes takes one round trip, not three. Flushes; the caller commits.
    """
    problems = []
    if not version or len(version) > 32:
        problems.append("version must be 1 to 32 characters")
    if not recorded_by.strip():
        problems.append("recorded_by must name who recorded it")
    absent = [m for m in MACHINES if m not in machines]
    unknown = sorted(set(machines) - set(MACHINES))
    if absent:
        problems.append(f"a version covers every machine; missing {', '.join(absent)}")
    if unknown:
        problems.append(f"unknown machine {', '.join(unknown)}")
    for machine in MACHINES:
        if machine in machines:
            problems.extend(_problems(machine, machines[machine]))
    if version and await session.get(MachineLimit, (version, MACHINES[0])) is not None:
        problems.append(f"version {version!r} exists; versions are never edited, add a new one")
    if problems:
        raise MachineLimitsError("; ".join(problems))

    rows = [
        MachineLimit(
            version=version,
            machine=machine,
            max_weight_kg=machines[machine]["max_weight_kg"],
            max_size_class=machines[machine]["max_size_class"],
            hazmat_or_liquid_allowed=machines[machine]["hazmat_or_liquid_allowed"],
            needs_step_free_path=machines[machine]["needs_step_free_path"],
            needs_continuous_sidewalk=machines[machine]["needs_continuous_sidewalk"],
            needs_open_ground=machines[machine]["needs_open_ground"],
            max_walk_distance_m=machines[machine]["max_walk_distance_m"],
            basis=machines[machine]["basis"],
            source=machines[machine]["source"].strip(),
            recorded_by=recorded_by.strip(),
        )
        for machine in MACHINES
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def machine_limits_versions(session: AsyncSession) -> list[tuple[str, datetime, list[str]]]:
    """Every version, oldest first, with when it was recorded and the basis of
    each machine's row in `MACHINES` order."""
    rows = (
        await session.execute(select(MachineLimit).order_by(MachineLimit.created_at, MachineLimit.version))
    ).scalars().all()
    versions: dict[str, tuple[datetime, dict[str, str]]] = {}
    for row in rows:
        versions.setdefault(row.version, (row.created_at, {}))[1][row.machine] = row.basis
    return [(v, at, [bases.get(m, "-") for m in MACHINES]) for v, (at, bases) in versions.items()]


async def machine_limits(session: AsyncSession, version: str | None = None) -> dict[str, MachineLimit]:
    """One version's rows by machine; the most recently recorded if none is named.

    Empty if the version does not exist."""
    if version is None:
        version = (
            await session.execute(
                select(MachineLimit.version)
                .order_by(MachineLimit.created_at.desc(), MachineLimit.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if version is None:
            return {}
    rows = (
        await session.execute(select(MachineLimit).where(MachineLimit.version == version))
    ).scalars().all()
    return {row.machine: row for row in rows}


def limits_document(limits: dict[str, MachineLimit]) -> dict[str, Any]:
    """A version as plain data: what `ml/` reads, and what `add` reads back."""
    first = next(iter(limits.values()))
    return {
        "version": first.version,
        "machines": {
            machine: {
                "max_weight_kg": float(row.max_weight_kg),
                "max_size_class": row.max_size_class,
                "hazmat_or_liquid_allowed": row.hazmat_or_liquid_allowed,
                "needs_step_free_path": row.needs_step_free_path,
                "needs_continuous_sidewalk": row.needs_continuous_sidewalk,
                "needs_open_ground": row.needs_open_ground,
                "max_walk_distance_m": row.max_walk_distance_m,
                "basis": row.basis,
                "source": row.source,
            }
            for machine, row in sorted(limits.items(), key=lambda kv: MACHINES.index(kv[0]))
        },
    }
