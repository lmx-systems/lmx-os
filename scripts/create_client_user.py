"""
Creates (or resets the password for) a client-portal user
(app/models/client_user.py) - the out-of-band provisioning and
lockout-recovery path for multi-user client accounts (docs/ROADMAP.md
C4).

Day to day, a client's own admin invites and manages the rest of that
client's users through the portal itself (POST /client/users) - no ops
involvement needed. This script exists for the two cases that can't:
seeding a client's very first admin outside the onboarding endpoint, and
recovering a client that's locked itself out (e.g. its only admin was
deactivated, or forgot the password). It mirrors scripts/create_ops_user.py.

Safe to re-run for an existing email - updates the password/name and
reactivates the account (is_active=True), so it doubles as "reset this
user's password" or "un-revoke them." Omitting --role on a re-run leaves
an existing user's role untouched, so a password reset never silently
demotes an admin. --client-id is required only when creating a brand-new
user (a client user must belong to a client); it's ignored on a re-run,
which already knows the user's client.

Usage:
    python -m scripts.create_client_user --email ap@acme.example --password "..." \\
        --name "AP Contact" --client-id <client-uuid> --role admin
    python -m scripts.create_client_user --email ap@acme.example --password "..." --name "AP Contact"

Requires DATABASE_URL to point at the stack to create the user in
(defaults in app/config.py match `docker compose up`'s port mappings).
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.client_auth.passwords import hash_password
from app.config import settings
from app.models.client import Client
from app.models.client_user import CLIENT_ADMIN_ROLE, CLIENT_USER_ROLES, ClientUser


async def _create_or_update(
    email: str, password: str, name: str, client_id: str | None, role: str | None
) -> str:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            result = await session.execute(select(ClientUser).where(ClientUser.email == email))
            user = result.scalar_one_or_none()

            if user is None:
                if client_id is None:
                    raise SystemExit("--client-id is required to create a new client user")
                if await session.get(Client, uuid.UUID(client_id)) is None:
                    raise SystemExit(f"No client exists with id {client_id}")
                user = ClientUser(
                    client_id=uuid.UUID(client_id),
                    email=email,
                    password_hash=hash_password(password),
                    name=name,
                    role=role or CLIENT_ADMIN_ROLE,
                    is_active=True,
                )
                session.add(user)
                await session.commit()
                return "created"

            user.password_hash = hash_password(password)
            user.name = name
            if role is not None:
                user.role = role
            user.is_active = True
            await session.commit()
            return "updated"
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--client-id", default=None, help="Required only when creating a new user.")
    parser.add_argument(
        "--role", choices=CLIENT_USER_ROLES, default=None,
        help="Defaults to 'admin' for a brand-new user; leaves an existing user's role untouched if omitted.",
    )
    args = parser.parse_args()

    result = asyncio.run(
        _create_or_update(args.email, args.password, args.name, args.client_id, args.role)
    )
    print(f"Client user {args.email} {result}.")


if __name__ == "__main__":
    main()
