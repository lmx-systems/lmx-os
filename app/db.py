"""
Async SQLAlchemy engine/session management.

Postgres is system-of-record for orders, clients, shops, drivers, routes,
stops, and rules (Section 10 of the technical design). Anything that needs
sub-50ms reads on the hot re-optimization path lives in Redis instead
(see app/redis_client.py) - Postgres is not on that critical path.
"""
import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


def _schedule_enqueued_webhooks(session: AsyncSession) -> None:
    """Kick off delivery for any webhook enqueued during this session, once it is over.

    **Placed here, at the one point every session passes through, on purpose.** A
    status change writes an owed `WebhookDelivery` row inside the caller's
    transaction (app/webhooks/sink.py) and the sweep guarantees it eventually goes
    out - but "eventually" is bounded by the scheduler interval, and §1.4 wants
    write-back inside 30 seconds. This is what closes that gap without any call site
    having to remember: by the time a session teardown runs, the route has already
    committed or rolled back, so an attempt can never describe a transaction that
    didn't land. A rolled-back row simply isn't found by the delivery query.

    Fire-and-forget. If the task never runs - a recycled instance, a suspended
    process - the row is still pending and still due, and the sweep picks it up.
    That is the difference between this and the guarantee.

    Imported lazily because app/webhooks/delivery.py needs session_scope from this
    module.
    """
    from app.webhooks.sink import take_pending_delivery_ids

    delivery_ids = take_pending_delivery_ids(session)
    if not delivery_ids:
        return

    from app.webhooks.delivery import deliver_now

    # Held in a set so the task isn't garbage-collected mid-flight - asyncio only
    # keeps a weak reference, and a dropped task here would silently push every
    # delivery onto the slower sweep.
    task = asyncio.create_task(deliver_now(delivery_ids))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


_background_tasks: set[asyncio.Task] = set()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a request-scoped session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            _schedule_enqueued_webhooks(session)


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for use outside request handlers (e.g. background jobs)."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            # The dispatch optimizer advances order status through here rather than
            # through a request, so it needs the same hook - otherwise every
            # `assigned` webhook would wait for the sweep.
            _schedule_enqueued_webhooks(session)
