#!/usr/bin/env python3
"""Turn the control arm on for a customer, and record who stays out of it.

    python scripts/control_arm.py status  --client <uuid>
    python scripts/control_arm.py enrol   --client <uuid> --fraction 0.08 --contracted 2026-10-01
    python scripts/control_arm.py exclude --client <uuid> --receiver "12 mill road, springfield" \
                                          --reason "largest account; owner asked"
    python scripts/control_arm.py revoke  --client <uuid> --receiver "12 mill road, springfield"
    python scripts/control_arm.py withdraw --client <uuid>

`EXP-1` ships off behind a recorded contract date, and until now nothing could
record one. The columns are nullable with no default and no code in `app/` or
`scripts/` ever wrote them, so enrolling a customer meant SQL against
production. Same for `EXP-2`'s exclusions: a customer could be promised their
fragile dock stays out of the arm with no way to write that down.

**A command rather than a button, deliberately.** Enrolling a customer in an
experiment that gives a slice of their orders a worse service is a rare act tied
to a signed clause, and `--contracted` has to come off that clause. A button in
a dashboard invites somebody to press it; a command requires them to have the
date in their hand.

**The receiver key is the normalised delivery address**, the same key
`app/identity/` produces and `assign_arm` stratifies on - so `--receiver` takes
the address and normalises it here rather than asking anyone to type a key.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import AsyncSessionLocal  # noqa: E402
from app.experiment.arms import (  # noqa: E402
    AlreadyEnrolled,
    control_arm_is_live,
    enrol_control_arm,
    withdraw_control_arm,
)
from app.experiment.exclusions import (  # noqa: E402
    exclude_receiver,
    exclusion_impact,
    revoke_exclusion,
)
from app.identity import receiver_key_for  # noqa: E402
from app.models.client import Client  # noqa: E402


async def _client(session, client_id: str) -> Client:
    client = await session.get(Client, uuid.UUID(client_id))
    if client is None:
        raise SystemExit(f"no client {client_id}")
    return client


def _key(address: str) -> str:
    key = receiver_key_for(address)
    if key is None:
        raise SystemExit(
            f"{address!r} does not name a resolvable place, so it cannot identify "
            "a dock. An exclusion against it would silently cover nothing."
        )
    return key


async def _status(client_id: str) -> int:
    async with AsyncSessionLocal() as session:
        client = await _client(session, client_id)
        live = control_arm_is_live(client)
        print(f"client {client.id}")
        print(f"  arm            {'ON' if live else 'off'}")
        if client.control_arm_contracted_at:
            print(f"  contracted     {client.control_arm_contracted_at:%Y-%m-%d}")
            print(f"  fraction       {client.control_arm_fraction:.0%}")
        else:
            print("  contracted     - (no clause date recorded, so no order is enrolled)")
        impact = await exclusion_impact(session, client_id=client.id)
        print(f"  exclusions     {impact.live_exclusions} live, "
              f"{impact.revoked_exclusions} revoked")
        for receiver in impact.excluded_receivers:
            print(f"                 {receiver}")
    return 0


async def _enrol(client_id: str, fraction: float, contracted: str, replacing: bool) -> int:
    async with AsyncSessionLocal() as session:
        client = await _client(session, client_id)
        try:
            await enrol_control_arm(
                session, client, fraction=fraction,
                contracted_at=datetime.strptime(contracted, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                ),
                replacing=replacing,
            )
        except (AlreadyEnrolled, ValueError) as problem:
            raise SystemExit(str(problem)) from problem
        await session.commit()
        print(
            f"client {client.id} enrolled: {fraction:.0%} of orders dispatched as "
            f"they would have been, clause dated {contracted}.\n"
            "Nothing is retroactive - orders already ingested carry no arm."
        )
    return 0


async def _withdraw(client_id: str) -> int:
    async with AsyncSessionLocal() as session:
        client = await _client(session, client_id)
        await withdraw_control_arm(session, client)
        await session.commit()
        print(
            f"client {client.id} withdrawn. Assignments already made are untouched - "
            "they are evidence of what happened, and leaving does not unmake it."
        )
    return 0


async def _exclude(client_id: str, address: str, reason: str, by: str) -> int:
    async with AsyncSessionLocal() as session:
        client = await _client(session, client_id)
        exclusion = await exclude_receiver(
            session, client_id=client.id, receiver_key=_key(address),
            reason=reason, requested_by=by,
        )
        await session.commit()
        print(f"excluded {exclusion.receiver_key!r} from the arm: {exclusion.reason}")
        print(
            "The savings statement will disclose the share of volume this removes. "
            "A dock left out is usually the one that can least afford a slower "
            "service, so it is not a representative slice."
        )
    return 0


async def _revoke(client_id: str, address: str) -> int:
    async with AsyncSessionLocal() as session:
        client = await _client(session, client_id)
        revoked = await revoke_exclusion(
            session, client_id=client.id, receiver_key=_key(address)
        )
        await session.commit()
        if revoked is None:
            print("no live exclusion for that dock")
        else:
            print(
                f"{revoked.receiver_key!r} is back in the arm. The row stays, so a "
                "later statement can still explain the months it was out."
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("status", "withdraw"):
        p = sub.add_parser(name)
        p.add_argument("--client", required=True)

    enrol = sub.add_parser("enrol")
    enrol.add_argument("--client", required=True)
    enrol.add_argument("--fraction", type=float, required=True, help="0.05 to 0.10")
    enrol.add_argument("--contracted", required=True, help="YYYY-MM-DD, off the clause")
    enrol.add_argument("--replacing", action="store_true")

    exclude = sub.add_parser("exclude")
    exclude.add_argument("--client", required=True)
    exclude.add_argument("--receiver", required=True, help="the delivery address")
    exclude.add_argument("--reason", required=True)
    exclude.add_argument("--by", default="customer", choices=("customer", "lmx"))

    revoke = sub.add_parser("revoke")
    revoke.add_argument("--client", required=True)
    revoke.add_argument("--receiver", required=True)

    args = parser.parse_args()
    if args.command == "status":
        return asyncio.run(_status(args.client))
    if args.command == "enrol":
        return asyncio.run(
            _enrol(args.client, args.fraction, args.contracted, args.replacing)
        )
    if args.command == "withdraw":
        return asyncio.run(_withdraw(args.client))
    if args.command == "exclude":
        return asyncio.run(_exclude(args.client, args.receiver, args.reason, args.by))
    return asyncio.run(_revoke(args.client, args.receiver))


if __name__ == "__main__":
    raise SystemExit(main())
