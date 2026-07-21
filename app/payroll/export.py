"""
Pay-period export assembly (roadmap item A9, provider: Rippling).

Assembles per-driver pay inputs for a date window and hands them to the
configured payroll client (app/payroll/client.py). Deliberately assembles
BOTH candidate inputs - hours worked and completed drops - because the
pay formula (hourly, per-drop, or blend) is still an open business
decision; whichever formula lands, the data is already here.

Hours use the same route-span proxy as the driver app's earnings screen
(app/api/driver_routes.py's _route_hours): route accepted -> last stop
completed. Not a timesheet - flagged there, same caveat here.

No scheduler is wired to this yet on purpose: the pay schedule (weekly vs
biweekly, period boundaries) is one of B4's open sub-items. The manual
entry point is `assemble_and_submit(session, hub_id, start, end)` - a
scheduled caller is a small addition once the schedule is decided, same
shape as app/learning_loop/scheduler.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.driver import Driver
from app.models.route import Route
from app.models.stop import Stop
from app.payroll.client import PayPeriodInput, PayrollSubmissionResult, get_payroll_client

logger = get_logger(__name__)


def _route_hours(route: Route) -> float:
    return max((route.updated_at - route.created_at).total_seconds() / 3600, 0.0)


async def assemble_pay_period(
    session: AsyncSession, hub_id: str, period_start: datetime, period_end: datetime
) -> list[PayPeriodInput]:
    """Per-driver hours + completed dropoffs for [period_start, period_end)."""
    drivers_result = await session.execute(
        select(Driver).where(Driver.hub_id == uuid.UUID(hub_id))
    )
    drivers = list(drivers_result.scalars().all())
    if not drivers:
        return []

    routes_result = await session.execute(
        select(Route).where(
            Route.driver_id.in_([d.id for d in drivers]),
            Route.status == "completed",
            Route.updated_at >= period_start,
            Route.updated_at < period_end,
        )
    )
    routes = list(routes_result.scalars().all())
    route_ids = [r.id for r in routes]

    drops_by_route: dict = {}
    if route_ids:
        drops_result = await session.execute(
            select(Stop.route_id, func.count())
            .where(
                Stop.route_id.in_(route_ids),
                Stop.stop_type == "dropoff",
                Stop.status == "completed",
            )
            .group_by(Stop.route_id)
        )
        drops_by_route = dict(drops_result.all())

    hours_by_driver: dict = {}
    drops_by_driver: dict = {}
    for route in routes:
        hours_by_driver[route.driver_id] = hours_by_driver.get(route.driver_id, 0.0) + _route_hours(route)
        drops_by_driver[route.driver_id] = drops_by_driver.get(route.driver_id, 0) + drops_by_route.get(
            route.id, 0
        )

    return [
        PayPeriodInput(
            driver_id=str(driver.id),
            payroll_employee_id=driver.payroll_employee_id,
            period_start=period_start.date().isoformat(),
            period_end=period_end.date().isoformat(),
            hours_worked=round(hours_by_driver.get(driver.id, 0.0), 2),
            completed_drops=drops_by_driver.get(driver.id, 0),
        )
        for driver in drivers
        # Drivers with zero activity in the period aren't submitted - a W2
        # employee with no routes has nothing for LMX to report; base pay
        # (if any) is Rippling's side of the fence.
        if hours_by_driver.get(driver.id) or drops_by_driver.get(driver.id)
    ]


async def assemble_and_submit(
    session: AsyncSession, hub_id: str, period_start: datetime, period_end: datetime
) -> PayrollSubmissionResult:
    inputs = await assemble_pay_period(session, hub_id, period_start, period_end)
    client = get_payroll_client()
    result = await client.submit_pay_period(inputs)
    logger.info(
        "payroll_export_completed",
        hub_id=hub_id,
        provider=result.provider,
        submitted=len(result.submitted),
        skipped_unlinked=len(result.skipped_unlinked),
    )
    return result
