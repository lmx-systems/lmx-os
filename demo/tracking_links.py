"""Print the recipient tracking links for recently delivered demo orders.

    python -m demo.tracking_links

**A demo tool, and the only thing in `demo/` that touches the database
directly.** Everything else goes through real HTTP on purpose, so this needs a
reason.

The reason is that a tracking token is disclosed in exactly one place - the SMS
`send_tracking_link_to_recipient` sends - and nothing else exposes it. Not the
ops console, not the client portal, not even to the client who owns the order.
That is a deliberate and tight design: a tracking link is a capability, and
anybody holding it can see the delivery photo. Widening an API so a demo could
find one would be trading a real boundary for a convenience.

So the link comes out of the database, here, where it is obviously a demo
affordance and not a product surface. In front of a customer the recipient gets
it by text, which is the whole point of minting it at the moment it is first
disclosed.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.order import Order, OrderStatus


async def _links(limit: int, portal_base_url: str) -> int:
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as session:
            orders = (
                await session.execute(
                    select(Order)
                    .where(
                        Order.status == OrderStatus.delivered,
                        Order.tracking_token.isnot(None),
                    )
                    .order_by(Order.delivered_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    if not orders:
        print(
            "No delivered order has a tracking token.\n\n"
            "A token is minted only when the order carries a recipient phone - "
            "see `send_tracking_link_to_recipient`. If the manifest had no phone "
            "column, nothing was ever texted and no link exists. `demo/"
            "run_full_loop.py`'s manifest carries one.",
            file=sys.stderr,
        )
        return 1

    for order in orders:
        print(f"{order.source_order_ref}  {portal_base_url}/track/{order.tracking_token}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--portal-base-url",
        default=settings.portal_base_url,
        help="where the client portal is served from",
    )
    args = parser.parse_args()
    return asyncio.run(_links(args.limit, args.portal_base_url.rstrip("/")))


if __name__ == "__main__":
    raise SystemExit(main())
