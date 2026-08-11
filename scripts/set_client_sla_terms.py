"""
Record what a client was promised, and what missing it costs (docs/ROADMAP.md W3).

`client_sla_terms` is contract data - see app/models/client_sla_term.py for why it is not
a constant in a Python file. Nothing computes an SLA credit until a term exists for the
tier, and a tier with no term is reported as *unassessable* rather than clean, so **an
empty table means the contract is not being enforced** even though the machinery is.

This is the out-of-band way to fill it, matching scripts/create_ops_user.py's shape.
Day to day these go in through `PUT /admin/clients/{id}/sla-terms`; this exists for
onboarding a client from a terminal, and for seeding a fresh deployment where no ops user
has been created yet.

Usage:

    .venv/bin/python -m scripts.set_client_sla_terms --client-id <uuid> --list

    .venv/bin/python -m scripts.set_client_sla_terms --client-id <uuid> --placeholders

    .venv/bin/python -m scripts.set_client_sla_terms \\
        --client-id <uuid> \\
        --term HOT_SHOT:60:100 \\
        --term T1:90:50 \\
        --term T2:180:25 \\
        --term T3:1440:0

`--placeholders` applies `PLACEHOLDER_SLA_TERMS` - a reasoned starting point nobody has
agreed to, openly provisional, there because an empty table means no breach is assessable
and the contract goes unenforced while looking fine. Replace them with what customer #1
signs (docs/ROADMAP.md E11, B2).

Each --term is TIER:DELIVERY_TARGET_MINUTES:CREDIT_PERCENT, optionally with
:MIN_CENTS:MAX_CENTS. The target is measured from when the order reached us.

Re-running updates an existing tier rather than adding a second row. **Changing a term does
not restate an issued invoice** - credits are frozen on the statement that charged them, so
last quarter's numbers do not move when this quarter's contract does.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from app.db import session_scope
from app.models.client import Client
from app.models.client_sla_term import PLACEHOLDER_SLA_TERMS, ClientSlaTerm


def _parse_term(raw: str) -> dict:
    parts = raw.split(":")
    if len(parts) < 3:
        raise argparse.ArgumentTypeError(
            f"{raw!r} should be TIER:TARGET_MINUTES:CREDIT_PERCENT"
            " (optionally :MIN_CENTS:MAX_CENTS)"
        )
    tier = parts[0].strip().upper()
    try:
        target = int(parts[1])
        percent = int(parts[2])
        minimum = int(parts[3]) if len(parts) > 3 and parts[3] else None
        maximum = int(parts[4]) if len(parts) > 4 and parts[4] else None
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} has a non-numeric field") from exc

    if target <= 0:
        raise argparse.ArgumentTypeError(f"{tier}: the delivery target must be positive")
    if not 0 <= percent <= 100:
        raise argparse.ArgumentTypeError(f"{tier}: the credit percent must be 0-100")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise argparse.ArgumentTypeError(f"{tier}: the credit minimum exceeds the maximum")

    return {
        "sla_tier": tier,
        "delivery_target_minutes": target,
        "credit_percent": percent,
        "credit_minimum_cents": minimum,
        "credit_maximum_cents": maximum,
    }


async def _run(client_id: str, terms: list[dict], show: bool) -> int:
    async with session_scope() as session:
        client = await session.get(Client, uuid.UUID(client_id))
        if client is None:
            print(f"No client with id {client_id}", file=sys.stderr)
            return 1

        for spec in terms:
            existing = (
                await session.execute(
                    select(ClientSlaTerm).where(
                        ClientSlaTerm.client_id == client.id,
                        ClientSlaTerm.sla_tier == spec["sla_tier"],
                    )
                )
            ).scalar_one_or_none()

            if existing is None:
                session.add(ClientSlaTerm(client_id=client.id, **spec))
                print(f"  + {spec['sla_tier']}")
            else:
                for field, value in spec.items():
                    setattr(existing, field, value)
                print(f"  ~ {spec['sla_tier']} (updated)")

        if terms:
            await session.flush()

        if show or terms:
            rows = (
                (
                    await session.execute(
                        select(ClientSlaTerm)
                        .where(ClientSlaTerm.client_id == client.id)
                        .order_by(ClientSlaTerm.sla_tier)
                    )
                )
                .scalars()
                .all()
            )
            print(f"\nSLA terms for {client.name}:")
            if not rows:
                # Said plainly, because the failure mode is silent: with no terms nothing
                # is ever credited and every order reports as unassessable.
                print("  (none - no SLA credit will ever be charged for this client)")
            for row in rows:
                credit = f"{row.credit_percent}%"
                if row.credit_minimum_cents:
                    credit += f", min {row.credit_minimum_cents}c"
                if row.credit_maximum_cents:
                    credit += f", max {row.credit_maximum_cents}c"
                print(
                    f"  {row.sla_tier:<9} deliver within {row.delivery_target_minutes:>5} min"
                    f"   credit {credit}"
                )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument(
        "--term",
        action="append",
        default=[],
        type=_parse_term,
        help="TIER:TARGET_MINUTES:CREDIT_PERCENT[:MIN_CENTS[:MAX_CENTS]]",
    )
    parser.add_argument(
        "--placeholders",
        action="store_true",
        help="apply PLACEHOLDER_SLA_TERMS - provisional, not agreed by anyone (E11)",
    )
    parser.add_argument("--list", action="store_true", help="show what's on file and exit")
    args = parser.parse_args()

    if args.placeholders:
        # Explicit terms win, so --placeholders can seed the tiers nobody has negotiated
        # yet while a real one overrides its tier in the same command.
        chosen = {term["sla_tier"] for term in args.term}
        args.term = [
            {
                "sla_tier": placeholder.sla_tier,
                "delivery_target_minutes": placeholder.delivery_target_minutes,
                "credit_percent": placeholder.credit_percent,
                "credit_minimum_cents": None,
                "credit_maximum_cents": None,
            }
            for placeholder in PLACEHOLDER_SLA_TERMS
            if placeholder.sla_tier not in chosen
        ] + args.term
        print("Applying PLACEHOLDER terms - provisional, not agreed by anyone (E11).")

    if not args.term and not args.list:
        parser.error("give at least one --term, --placeholders, or --list")

    return asyncio.run(_run(args.client_id, args.term, args.list))


if __name__ == "__main__":
    sys.exit(main())
