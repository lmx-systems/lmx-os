"""Is the arm still an arm? (`EXP-3`)

Done when *"a skewed or contaminated arm alerts before a statement is
generated"* - and `STL-1` is now the statement, so this is a gate rather than a
dashboard. A savings claim rests entirely on the control group being a fair
sample dispatched the customer's old way. If it stopped being one, every number
downstream is confidently wrong, and confidently wrong is the only failure mode
here that reaches a customer.

## What it checks, and why each one is a way the claim dies

**Verification.** `verify_assignment` recomputes an arm from its stored salt,
block and position. A row that does not verify did not get that way by being
edited - `experiment_assignments` refuses UPDATE by trigger, which is EXP-1's
whole point. It got that way by **arriving** wrong: a restore from a dump, a
backfill, a migration that recreated the table, a script that went round the
ORM. One is an incident; a handful means the arm is not evidence of anything.

**Skew.** The realised control share against the contracted fraction, by Wilson
interval. `DATA_NEED_BRIEF.md` is explicit that the normal approximation is
anti-conservative at small proportions and fails on the side that matters, and a
5-10% arm is exactly that regime, so Wald is not used here either.

**The per-dock guarantee.** `EXP-2` promises no dock takes more than one control
order per block. A dock over that means the advisory lock did not hold, and the
promise made to the customer before they signed is no longer true.

**Position collisions.** Two assignments at the same position in the same dock's
block. Direct evidence of the race the lock exists to prevent, and it is worth
detecting separately from the guarantee because it can occur without breaching
it and is the earlier warning.

**Differential coverage - the subtle one.** If control orders are less likely to
end up with a recorded cost than treatment orders, the comparison runs on a
biased subsample even though the assignment was perfectly fair. Nothing about
the arm looks wrong; the arithmetic downstream is simply computed on the orders
that happened to get measured. This is the check most likely to fire for a real
reason, and the least likely to be noticed without one.

**Terms drift.** Assignments in one window made under different fractions or
different contract dates. Pooling them into a single comparison silently mixes
two experiments, and the statement would describe neither.

## What it cannot check, which is the contamination that matters most

Nothing in the data records that dispatch *declined to act* on a control order.
The arm means "dispatched as the customer would have" and the only evidence we
hold is the label saying so. A control order that was quietly held and batched
like any other looks identical to one that was not.

Detecting that needs the dispatch path to write down its abstention - a `REC-1`
extension, not something this monitor can infer - so it is reported as a
standing limitation on every run rather than left for somebody to discover after
quoting a number. A monitor that listed six checks and stayed silent about the
seventh would be worse than no monitor, because it would read as a clean bill.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.experiment.arms import verify_assignment
from app.models.experiment_assignment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    EXPERIMENT_CONTROL_ARM,
    ExperimentAssignment,
)
from app.models.outcome_entry import KIND_COST, OutcomeEntry

SEVERITY_BLOCKS = "blocks"
SEVERITY_WARNS = "warns"
SEVERITY_NOTES = "notes"

CONFIDENCE = 0.95

# Below this many assignments, skew is not tested: a 5-10% arm over forty orders
# holds two or three control drops, and any share computed from that is noise
# with a decimal point. Silence here is "not enough to judge", which the report
# says rather than implying a pass.
MIN_ASSIGNMENTS_FOR_SKEW = 100

# How far the control arm's coverage may fall behind the treatment arm's before
# the comparison is running on a biased subsample. Ten points is a judgement,
# and it is named so it can be argued with rather than buried in a comparison.
MAX_COVERAGE_GAP = 0.10


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    detail: str
    numbers: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.check}: {self.detail}"


@dataclass
class IntegrityReport:
    client_id: object
    since: datetime
    until: datetime
    assignments: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SEVERITY_BLOCKS]

    @property
    def blocks_a_statement(self) -> bool:
        return bool(self.blocking)

    def why_blocked(self) -> str | None:
        if not self.blocking:
            return None
        return "; ".join(f.detail for f in self.blocking)


def wilson_interval(successes: int, trials: int, confidence: float = CONFIDENCE):
    """Wilson score interval for a proportion.

    Not Wald. `DATA_NEED_BRIEF.md` §4.3: the normal approximation "is
    anti-conservative in exactly the small-proportion regime and it fails on the
    side that matters". A control arm is 5-10% by contract, so that regime is
    the only one this function is ever used in.
    """
    if trials <= 0:
        return (0.0, 1.0)
    z = _z_for(confidence)
    phat = successes / trials
    denominator = 1 + z**2 / trials
    centre = (phat + z**2 / (2 * trials)) / denominator
    half = (
        z
        * math.sqrt(phat * (1 - phat) / trials + z**2 / (4 * trials**2))
        / denominator
    )
    return (max(0.0, centre - half), min(1.0, centre + half))


def _z_for(confidence: float) -> float:
    from statistics import NormalDist

    return NormalDist().inv_cdf(1 - (1 - confidence) / 2)


async def check_arm_integrity(
    session: AsyncSession, *, client_id, since: datetime, until: datetime
) -> IntegrityReport:
    """Every check, run over one window, with the limitation stated either way."""
    assignments = list(
        await session.scalars(
            select(ExperimentAssignment).where(
                ExperimentAssignment.client_id == client_id,
                ExperimentAssignment.experiment == EXPERIMENT_CONTROL_ARM,
                ExperimentAssignment.assigned_at >= since,
                ExperimentAssignment.assigned_at < until,
            )
        )
    )
    report = IntegrityReport(
        client_id=client_id, since=since, until=until, assignments=len(assignments)
    )
    # Stated on every run, pass or fail. A monitor that listed its checks and
    # stayed quiet about the one it cannot make would read as a clean bill.
    report.findings.append(
        Finding(
            check="dispatch-abstention-is-unverifiable",
            severity=SEVERITY_NOTES,
            detail=(
                "nothing records that dispatch declined to act on a control order, "
                "so an order that was quietly held and batched looks identical to "
                "one dispatched the customer's old way. This monitor cannot detect "
                "that; recording the abstention is a REC-1 extension"
            ),
        )
    )
    if not assignments:
        report.findings.append(
            Finding(
                check="no-assignments",
                severity=SEVERITY_NOTES,
                detail="no orders were assigned to an arm in this window",
            )
        )
        return report

    _check_verification(report, assignments)
    _check_skew(report, assignments)
    _check_per_dock_guarantee(report, assignments)
    _check_position_collisions(report, assignments)
    _check_terms_drift(report, assignments)
    await _check_differential_coverage(session, report, assignments)
    return report


def _check_verification(report: IntegrityReport, assignments: list) -> None:
    failed = [a for a in assignments if not verify_assignment(a)]
    if failed:
        report.findings.append(
            Finding(
                check="assignments-verify",
                severity=SEVERITY_BLOCKS,
                detail=(
                    f"{len(failed)} of {len(assignments)} assignments do not "
                    "recompute to the arm they are stored with. They cannot have "
                    "been edited - the table refuses UPDATE - so they arrived this "
                    "way: a restore, a backfill, or a write that went round the ORM"
                ),
                numbers={"failed": len(failed), "checked": len(assignments)},
            )
        )


def _check_skew(report: IntegrityReport, assignments: list) -> None:
    controls = sum(1 for a in assignments if a.arm == ARM_CONTROL)
    total = len(assignments)
    contracted = {a.control_fraction for a in assignments}
    if total < MIN_ASSIGNMENTS_FOR_SKEW:
        report.findings.append(
            Finding(
                check="control-share",
                severity=SEVERITY_NOTES,
                detail=(
                    f"{total} assignments is too few to judge the split against a "
                    f"{min(contracted):.0%} arm - not a pass, just not testable yet"
                ),
                numbers={"control": controls, "total": total},
            )
        )
        return
    low, high = wilson_interval(controls, total)
    expected = sum(contracted) / len(contracted)
    if not low <= expected <= high:
        report.findings.append(
            Finding(
                check="control-share",
                severity=SEVERITY_BLOCKS,
                detail=(
                    f"the arm ran at {controls / total:.1%} against a contracted "
                    f"{expected:.1%}; the {CONFIDENCE:.0%} interval is "
                    f"{low:.1%}-{high:.1%} and does not contain it"
                ),
                numbers={
                    "control": controls,
                    "total": total,
                    "observed": controls / total,
                    "contracted": expected,
                    "interval": (low, high),
                },
            )
        )


def _check_per_dock_guarantee(report: IntegrityReport, assignments: list) -> None:
    per_dock: dict = {}
    for a in assignments:
        if a.receiver_key is None or a.block_size is None:
            continue
        stats = per_dock.setdefault(a.receiver_key, {"n": 0, "control": 0, "size": a.block_size})
        stats["n"] += 1
        if a.arm == ARM_CONTROL:
            stats["control"] += 1

    breached = {
        key: stats
        for key, stats in per_dock.items()
        if stats["control"] > math.ceil(stats["n"] / stats["size"])
    }
    if breached:
        report.findings.append(
            Finding(
                check="per-dock-share",
                severity=SEVERITY_BLOCKS,
                detail=(
                    f"{len(breached)} dock(s) took more control orders than EXP-2 "
                    "guarantees. That promise was made to the customer before they "
                    "signed the clause"
                ),
                numbers={"docks": sorted(breached)[:10], "breached": len(breached)},
            )
        )


def _check_position_collisions(report: IntegrityReport, assignments: list) -> None:
    seen: dict = {}
    collisions = 0
    for a in assignments:
        if a.receiver_key is None or a.position_in_block is None:
            continue
        key = (a.receiver_key, a.block_index, a.position_in_block)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            collisions += 1
    if collisions:
        report.findings.append(
            Finding(
                check="position-collisions",
                severity=SEVERITY_WARNS,
                detail=(
                    f"{collisions} block position(s) were handed out twice - the "
                    "advisory lock that serialises the count did not hold. It does "
                    "not always breach the per-dock guarantee, and it is the "
                    "earlier warning that it will"
                ),
                numbers={"collisions": collisions},
            )
        )


def _check_terms_drift(report: IntegrityReport, assignments: list) -> None:
    fractions = {a.control_fraction for a in assignments}
    contracted = {a.contracted_at for a in assignments}
    if len(fractions) > 1:
        report.findings.append(
            Finding(
                check="terms-drift",
                severity=SEVERITY_BLOCKS,
                detail=(
                    f"assignments in this window were made under {len(fractions)} "
                    "different control fractions. Pooling them is two experiments "
                    "described as one, and the statement would describe neither"
                ),
                numbers={"fractions": sorted(fractions)},
            )
        )
    if len(contracted) > 1:
        report.findings.append(
            Finding(
                check="clause-drift",
                severity=SEVERITY_WARNS,
                detail=(
                    f"{len(contracted)} different contract dates appear on this "
                    "window's assignments - the clause changed mid-period"
                ),
                numbers={"dates": sorted(str(d) for d in contracted)},
            )
        )


async def _check_differential_coverage(
    session: AsyncSession, report: IntegrityReport, assignments: list
) -> None:
    """Are control orders as likely to be costed as treatment orders?

    The failure this catches leaves the assignment perfectly fair and the
    comparison biased anyway: if control orders systematically miss costing -
    dispatched differently, so a geofence never fires, so no cost is recorded -
    the arithmetic downstream runs on whichever orders happened to get measured.
    Nothing about the arm looks wrong.
    """
    order_ids = [a.order_id for a in assignments]
    costed = set(
        await session.scalars(
            select(OutcomeEntry.subject_id).where(
                OutcomeEntry.subject_id.in_(order_ids),
                OutcomeEntry.kind == KIND_COST,
            )
        )
    )
    by_arm: dict = {ARM_CONTROL: [0, 0], ARM_TREATMENT: [0, 0]}
    for a in assignments:
        bucket = by_arm.setdefault(a.arm, [0, 0])
        bucket[0] += 1
        if a.order_id in costed:
            bucket[1] += 1

    control_total, control_costed = by_arm[ARM_CONTROL]
    treatment_total, treatment_costed = by_arm[ARM_TREATMENT]
    if control_total < 20 or treatment_total < 20:
        return
    control_coverage = control_costed / control_total
    treatment_coverage = treatment_costed / treatment_total
    gap = treatment_coverage - control_coverage
    if abs(gap) > MAX_COVERAGE_GAP:
        report.findings.append(
            Finding(
                check="differential-coverage",
                severity=SEVERITY_BLOCKS,
                detail=(
                    f"{control_coverage:.0%} of control orders were costed against "
                    f"{treatment_coverage:.0%} of the rest. The assignment was fair "
                    "and the comparison is not: it would run on whichever orders "
                    "happened to get measured"
                ),
                numbers={
                    "control_coverage": control_coverage,
                    "treatment_coverage": treatment_coverage,
                    "gap": gap,
                },
            )
        )


def render(report: IntegrityReport) -> str:
    lines = [
        f"Arm integrity, client {report.client_id}",
        f"  {report.since:%Y-%m-%d} to {report.until:%Y-%m-%d}, "
        f"{report.assignments} assignments",
        "",
    ]
    for finding in report.findings:
        lines.append(f"  {finding}")
    lines.append("")
    if report.blocks_a_statement:
        lines.append("  A statement must not be generated from this window.")
    else:
        lines.append("  Nothing here blocks a statement.")
    return "\n".join(lines)
