"""An order nobody has: released for dispatch and placed with no driver.

One definition, read by the exceptions queue (`app/reporting/exceptions.py`,
`CON-4`) and by the playbook's rule for these orders (`app/playbook/sweep.py`,
`DEC-10`), so the two never disagree about which orders are waiting on a person.

Two ways in. A dispatcher released it and no cycle placed it: the override is
the only writer of `queued`. Or its hold ran out and the cycle released it,
found nobody, and left it `held` - it stays in the queue and is released again
every cycle, so its status never changes. Past its deadline by more than the
grace (`stuck_order_after_seconds`, the health check's), so an order the next
cycle is about to place is not counted. Live orders only: a backfill loads
history as `held` with a deadline long past, on purpose and never queued.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.config import settings
from app.models.order import INTAKE_LIVE, Order, OrderStatus


def unplaced_clause(now: datetime) -> ColumnElement[bool]:
    grace = timedelta(seconds=settings.stuck_order_after_seconds)
    return and_(
        Order.intake_mode == INTAKE_LIVE,
        or_(
            and_(Order.status == OrderStatus.queued, Order.requested_at < now - grace),
            and_(
                Order.status == OrderStatus.held,
                Order.hold_deadline.is_not(None),
                Order.hold_deadline < now - grace,
            ),
        ),
    )
