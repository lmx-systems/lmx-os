"""Run IDN-1..3 against a real dispatch export and report what happened.

    python scripts/load_identity_from_export.py path/to/customerTiming.csv --dry-run
    python scripts/load_identity_from_export.py path/to/customerTiming.csv --commit

This is the step that turns identity from mechanism into a number. Until it has
run, IDN-1's docks, IDN-2's merge queue and IDN-3's classification are all
correct in tests and unproven on anything real.

**It prints counts, never customer data.** The export holds the design
partner's account names, towns and postcodes, and CLAUDE.md's naming rule
covers logs as well as documents. Every figure below is an aggregate; if you
need to see the rows, query the database directly rather than making this
print them.

**`--dry-run` is the default** and rolls back. Loading a customer's account book
into a database is not something to do by accident, and the interesting output -
how many docks, how many merge candidates, what share classifies - is available
without keeping any of it.
"""
import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.identity import (  # noqa: E402
    classification_coverage,
    classify_unlabelled_locations,
    propose_duplicate_locations,
    resolve_location,
)
from app.ingestion.adapters.dispatch_export import accounts_in, parse_customer_timing  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.hub import Hub  # noqa: E402
from app.models.shop import Shop  # noqa: E402

# The load needs a client to hang shops off. Named generically on purpose - the
# real customer's name does not go into the database from this script, and a
# placeholder that looks like a placeholder is harder to mistake for real
# onboarding than one that looks like a company.
_IMPORT_CLIENT_NAME = "Historical export (design partner)"


async def _run(path: Path, commit: bool) -> int:
    stops = parse_customer_timing(path)
    accounts = accounts_in(stops)
    print(f"parsed            {len(stops)} stop rows, {len(accounts)} accounts")

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            hub = Hub(name="Historical import hub", lat=0.0, lng=0.0)
            session.add(hub)
            await session.flush()
            client = Client(hub_id=hub.id, name=_IMPORT_CLIENT_NAME, pos_system="flat_file")
            session.add(client)
            await session.flush()

            unresolvable = 0
            for account in accounts.values():
                try:
                    location = await resolve_location(
                        session, address=account.identity_address
                    )
                except ValueError:
                    # No usable address. Recorded as a shop with no dock, which
                    # is the honest state - see IDN-1.
                    unresolvable += 1
                    location = None
                session.add(
                    Shop(
                        client_id=client.id,
                        name=account.account_name or account.account_ref,
                        address=account.identity_address or account.account_ref,
                        lat=0.0,
                        lng=0.0,
                        external_ref=account.account_ref,
                        location_id=location.id if location else None,
                    )
                )
            await session.flush()
            print(f"docks created     {len(accounts) - unresolvable}"
                  f"  ({unresolvable} accounts had no usable address)")

            proposals = await propose_duplicate_locations(session)
            tiers = Counter(p.reason.split(" - ", 1)[0] for p in proposals)
            print(f"merge candidates  {len(proposals)}"
                  f"  {dict(sorted(tiers.items()))}")
            reasons = Counter(p.reason.split(" - ", 1)[-1] for p in proposals)
            for reason, count in reasons.most_common(8):
                print(f"                    {count:4}  {reason}")

            classified = await classify_unlabelled_locations(session)
            coverage = await classification_coverage(session)
            print(f"node classes      {classified['labelled']} of "
                  f"{classified['considered']} docks labelled")
            print(f"IDN-3 coverage    {coverage['unlabelled_pct']:.1f}% unlabelled "
                  f"(target <2%) - meets_target={coverage['meets_target']}")

            if commit:
                await session.commit()
                print("\ncommitted to", settings.database_url.rsplit("@", 1)[-1])
            else:
                await session.rollback()
                print("\ndry run - rolled back, nothing kept")
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="Customer Timing CSV")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if not args.export.exists():
        parser.error(f"{args.export} does not exist")
    return asyncio.run(_run(args.export, commit=args.commit))


if __name__ == "__main__":
    raise SystemExit(main())
