"""
Creates (or resets the password for) an ops-dashboard user
(app/models/ops_user.py) - the bootstrap mechanism for real per-account
ops auth (docs/ROADMAP.md S1). There's no self-service signup for
internal ops staff, by design (these are hand-provisioned employees, not
a public signup flow), and no account exists yet the first time this
runs against a fresh stack, so logging into dashboard/ at all depends on
running this at least once.

Safe to re-run for an existing email - updates the password/name and
reactivates the account (is_active=True) rather than erroring, so this
also doubles as "reset my password" or "un-revoke this account."

Usage:
    python -m scripts.create_ops_user --email you@lmxit.com --password "..." --name "Your Name"

Requires DATABASE_URL to point at the stack to create the user in
(defaults in app/config.py match `docker compose up`'s port mappings).
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.client_auth.passwords import hash_password
from app.config import settings
from app.models.ops_user import OpsUser


async def _create_or_update(email: str, password: str, name: str) -> str:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            result = await session.execute(select(OpsUser).where(OpsUser.email == email))
            ops_user = result.scalar_one_or_none()

            if ops_user is None:
                ops_user = OpsUser(email=email, password_hash=hash_password(password), name=name, is_active=True)
                session.add(ops_user)
                await session.commit()
                return "created"

            ops_user.password_hash = hash_password(password)
            ops_user.name = name
            ops_user.is_active = True
            await session.commit()
            return "updated"
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    result = asyncio.run(_create_or_update(args.email, args.password, args.name))
    print(f"Ops user {args.email} {result}.")


if __name__ == "__main__":
    main()
