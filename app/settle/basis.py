"""What a statement was calculated under, frozen and versioned (`STL-2`).

*"Baseline changes need sign-off from both sides and are versioned."*

**The problem `--recompute` created.** `STL-1` reads costs from the outcome
ledger, and `scripts/settle_month.py --recompute` supersedes them when an input
changes - a driver's rate finally recorded, a geofence event arriving late. Both
behaviours are right on their own and together they mean a statement is not
reproducible: the same command, the same period, a different number, and nothing
recording which one the customer is holding.

So issuing a statement freezes its definition and fingerprints the costs it
read. Re-running later either reproduces the figure or says exactly what moved -
which is the difference between a conversation and an argument.

Same shape as `REC-1`, deliberately, down to reusing its
`canonical_inputs_hash`: freeze the values, hash them, and compare rather than
trust. A settlement is a decision about money, and the discipline that protects
a dispatch decision is the one it needs.

**The fingerprint is the part that earns its place.** Hashing the definition
alone would catch somebody changing the method and miss the case that actually
happens: the method stays identical and the underlying costs are recomputed
beneath it. A digest over the (order, cost) pairs catches that, and because it
is per order it can say *which* orders moved rather than only that something
did.

**Signatures are recorded, not enforced.** Code cannot make a customer agree.
It can refuse to call a basis agreed until both sides are on it, and it can make
an unsigned change visible instead of routine - which is the whole of what
sign-off can mean inside a database.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settlement_basis import SIDE_CUSTOMER, SIDE_LMX, SIDES, SettlementBasis
from app.record.decisions import canonical_inputs_hash
from app.settle.statement import SavingsStatement

# Bumped when the shape of `inputs` changes. A reader that cannot recognise the
# shape should say so rather than guess at a field that moved - the same reason
# `REC-1`'s frozen inputs carry one.
BASIS_VERSION = 1


class NotSigned(RuntimeError):
    """Raised when something requires an agreed basis and only one side signed."""


def cost_fingerprint(costed_orders: dict) -> str:
    """A digest over what the statement actually read.

    Sorted by order id so two runs that saw the same costs hash alike whatever
    order the rows came back in. Without that the digest would be a nonce and
    the comparison it exists for would never match - the same trap
    `freeze_plan_inputs` avoids by ordering stops and drivers.
    """
    pairs = sorted((str(k), int(v)) for k, v in costed_orders.items())
    return hashlib.sha256(
        "|".join(f"{order}:{cents}" for order, cents in pairs).encode()
    ).hexdigest()


def freeze_statement_basis(statement: SavingsStatement) -> dict:
    """The definition this statement rested on, as plain values.

    Values, not references. A later edit to a client row must not be able to
    change what this says was agreed - which is the same reason
    `ExperimentAssignment` copies the contracted date rather than joining to it.
    """
    comparison = statement.comparison
    return {
        "version": BASIS_VERSION,
        "period": [
            statement.period_start.isoformat(),
            statement.period_end.isoformat(),
        ],
        # How a cost was arrived at. The method, not the numbers - so a change
        # here is a change of definition rather than of weather.
        "cost_method": {
            "unit": "driver-day, loaded",
            "attribution": "own time plus pro-rata overhead",
            "excludes": ["fuel", "vehicle", "overhead beyond driver time"],
        },
        "comparison_floor_drops_per_arm": 30,
        "exclusions": list(
            statement.exclusions.excluded_receivers if statement.exclusions else ()
        ),
        # What came out, so reproduction can be checked without recomputing.
        "figures": {
            "drops": statement.drops,
            "costed_drops": statement.costed_drops,
            "cost_per_drop_cents": statement.cost_per_drop_cents,
            "difference_cents": comparison.difference_cents if comparison else None,
            "low_cents": comparison.low_cents if comparison else None,
            "high_cents": comparison.high_cents if comparison else None,
            "shows_a_saving": comparison.shows_a_saving if comparison else False,
        },
    }


@dataclass
class Reproduction:
    """Whether an issued statement still produces the figure it was issued with."""

    basis_version: int
    definition_matches: bool
    costs_match: bool
    recosted_orders: list = field(default_factory=list)
    changed_figures: dict = field(default_factory=dict)

    @property
    def reproduces(self) -> bool:
        return self.definition_matches and self.costs_match

    def explain(self) -> str:
        if self.reproduces:
            return f"Basis v{self.basis_version} still reproduces exactly."
        parts = []
        if not self.definition_matches:
            parts.append(
                "the definition changed: "
                + ", ".join(
                    f"{k} was {v['was']!r}, now {v['now']!r}"
                    for k, v in self.changed_figures.items()
                )
                if self.changed_figures
                else "the definition changed"
            )
        if not self.costs_match:
            parts.append(
                f"{len(self.recosted_orders)} order(s) were recosted after issue"
            )
        return f"Basis v{self.basis_version} no longer reproduces: " + "; ".join(parts)


async def live_basis(
    session: AsyncSession, *, client_id, period_start: datetime, period_end: datetime
) -> SettlementBasis | None:
    return await session.scalar(
        select(SettlementBasis).where(
            SettlementBasis.client_id == client_id,
            SettlementBasis.period_start == period_start,
            SettlementBasis.period_end == period_end,
            SettlementBasis.superseded_at.is_(None),
        )
    )


async def issue(
    session: AsyncSession,
    statement: SavingsStatement,
    *,
    now: datetime | None = None,
) -> SettlementBasis:
    """Record what this statement was calculated under.

    Idempotent on an unchanged definition: re-issuing the same period with the
    same method and the same costs returns the existing basis rather than
    minting v2 of something identical. A version number that incremented every
    time somebody re-ran a script would stop meaning "the definition changed",
    which is the only thing it is for.
    """
    now = now or datetime.now(timezone.utc)
    inputs = freeze_statement_basis(statement)
    inputs_hash = canonical_inputs_hash(inputs)
    fingerprint = cost_fingerprint(statement.costed_orders)

    existing = await live_basis(
        session,
        client_id=statement.client_id,
        period_start=statement.period_start,
        period_end=statement.period_end,
    )
    if existing is not None:
        if (
            existing.inputs_hash == inputs_hash
            and existing.cost_fingerprint == fingerprint
        ):
            return existing
        # Something moved. The previous basis is superseded rather than edited,
        # because "we changed how this was calculated in October" has to survive
        # the change - and if it was agreed, the new one is not yet.
        existing.superseded_at = now

    next_version = int(
        await session.scalar(
            select(func.coalesce(func.max(SettlementBasis.version), 0) + 1).where(
                SettlementBasis.client_id == statement.client_id
            )
        )
    )
    basis = SettlementBasis(
        client_id=statement.client_id,
        version=next_version,
        period_start=statement.period_start,
        period_end=statement.period_end,
        issued_at=now,
        inputs=inputs,
        inputs_hash=inputs_hash,
        cost_fingerprint=fingerprint,
        costs={str(k): int(v) for k, v in statement.costed_orders.items()},
    )
    session.add(basis)
    await session.flush()
    return basis


def reproduce(basis: SettlementBasis, statement: SavingsStatement) -> Reproduction:
    """Does this statement still match the basis it was issued under?"""
    inputs = freeze_statement_basis(statement)
    definition_matches = canonical_inputs_hash(inputs) == basis.inputs_hash
    fingerprint = cost_fingerprint(statement.costed_orders)
    costs_match = fingerprint == basis.cost_fingerprint

    recosted = []
    if not costs_match:
        was = basis.costs or {}
        now_costs = {str(k): int(v) for k, v in statement.costed_orders.items()}
        recosted = sorted(
            set(was) ^ set(now_costs)
            | {order for order in set(was) & set(now_costs) if was[order] != now_costs[order]}
        )

    changed = {}
    if not definition_matches:
        before = (basis.inputs or {}).get("figures", {})
        after = inputs["figures"]
        for key, value in after.items():
            if before.get(key) != value:
                changed[key] = {"was": before.get(key), "now": value}

    return Reproduction(
        basis_version=basis.version,
        definition_matches=definition_matches,
        costs_match=costs_match,
        recosted_orders=recosted,
        changed_figures=changed,
    )


async def sign(
    session: AsyncSession,
    basis: SettlementBasis,
    *,
    side: str,
    who: str,
    now: datetime | None = None,
) -> SettlementBasis:
    """Record one side's agreement. Both are needed before it counts."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    if not who or not who.strip():
        raise ValueError("a signature needs a name - 'signed' by nobody is not a record")
    now = now or datetime.now(timezone.utc)
    if side == SIDE_LMX:
        basis.lmx_signed_by, basis.lmx_signed_at = who.strip(), now
    else:
        basis.customer_signed_by, basis.customer_signed_at = who.strip(), now
    await session.flush()
    return basis


def require_agreed(basis: SettlementBasis | None) -> SettlementBasis:
    """Raise unless both sides have signed.

    For the callers that should not proceed on a draft. Kept separate from
    `sign` so the refusal is a decision at the call site rather than a side
    effect of recording a signature.
    """
    if basis is None:
        raise NotSigned("no basis has been issued for this period")
    if not basis.is_agreed:
        missing = SIDE_LMX if basis.lmx_signed_at is None else SIDE_CUSTOMER
        raise NotSigned(
            f"basis v{basis.version} is not agreed - {missing} has not signed. "
            "One signature is a draft."
        )
    return basis
