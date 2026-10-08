"""Print the recipient tracking links for recently delivered demo orders.

    python -m demo.tracking_links

**A demo tool, and the only thing in `demo/` that touches the database
directly.** Everything else goes through real HTTP on purpose. The links are in
the client portal too - each order's page shows its tracking link once the parts
are collected, because LMX sends no texts and the client is who forwards it. This
prints them for a stack where nobody has logged into the portal.
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
            "A token is minted when an order's parts are collected, so an order "
            "that never reached pickup has none.",
            file=sys.stderr,
        )
        return 1

    for order in orders:
        print(f"{order.source_order_ref}  {portal_base_url.rstrip('/')}/track?token={order.tracking_token}")
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
