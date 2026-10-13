"""Counting what people do from the console (`DEC-7`, decision D-SEAT).

"Person touches per 100 orders" is the number that says how far LMX is from
nobody running the day. Every ops write a person makes is recorded here, one
`OpsTouch` row each, by `record_ops_touch` - a dependency on the three routers
that carry ops writes (`app/api/routes.py`, `app/api/admin_routes.py`,
`app/ingestion/router.py`).

## Why a router dependency, and not a line in each handler

There are 46 ops writes, and a count that relies on each handler remembering to
call it is wrong by however many forgot. A dependency on the router covers every
write on it, including ones not written yet. It records only after the handler
returned: FastAPI throws a handler's exception into the dependency at its
`yield`, so a refused write - a 403, a 409 - is not a touch, and the code after
the `yield` never runs.

**Its own session**, not the request's. The handler has committed by the time
this runs; writing the touch on the request's session would also commit
anything the handler left pending, and a failure here would surface in its
transaction. A touch that fails to record is logged and dropped - the write it
describes has already happened, and the person must not be told it failed.

## Categories

`TOUCH_CATEGORIES` names each write's kind of work. **`day`** is running the
day and **`record`** is fixing the record: the two the playbook exists to take
over, and the headline rate. **`policy`** is setting the rules the system runs
by, and **`business`** is onboarding, money and accounts; both are counted, so
nothing a person does is invisible, but neither is dispatching.
`tests/test_ops_touches_cover_every_write.py` fails when a write has no
category, so a new endpoint is classified when it is written.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models.client import Client
from app.models.driver import Driver
from app.models.ops_touch import (
    CATEGORY_BUSINESS,
    CATEGORY_DAY,
    CATEGORY_POLICY,
    CATEGORY_RECORD,
    CATEGORY_UNCLASSIFIED,
    OpsTouch,
)
from app.models.order import Order
from app.models.return_item import ReturnItem
from app.models.route import Route
from app.models.stop import Stop
from app.ops_auth.tokens import InvalidOpsToken, decode_token

logger = structlog.get_logger(__name__)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

TOUCH_CATEGORIES: dict[str, str] = {
    # Running the day: what a dispatcher did, and what the playbook takes over.
    "override_order": CATEGORY_DAY,
    "assign_order": CATEGORY_DAY,
    "resolve_order": CATEGORY_DAY,
    "cancel_order_as_dispatch": CATEGORY_DAY,
    "mark_return_returned": CATEGORY_DAY,
    "reschedule_return": CATEGORY_DAY,
    "admin_reply_to_driver": CATEGORY_DAY,
    "ingest_order_endpoint": CATEGORY_DAY,
    "run_optimizer_cycle": CATEGORY_DAY,
    "run_learning_loop_nightly_job": CATEGORY_DAY,
    "upsert_driver_state": CATEGORY_DAY,
    "upsert_driver_location": CATEGORY_DAY,
    # Fixing the record.
    "settle_site_fact_dispute": CATEGORY_RECORD,
    "label_record": CATEGORY_RECORD,
    "label_dock": CATEGORY_RECORD,
    "confirm_merge_proposal": CATEGORY_RECORD,
    "reject_merge_proposal": CATEGORY_RECORD,
    "revert_applied_merge": CATEGORY_RECORD,
    "redo_reverted_merge": CATEGORY_RECORD,
    "record_order_consequence": CATEGORY_RECORD,
    "resolve_linkage_flag": CATEGORY_RECORD,
    "import_dock_log_submission": CATEGORY_RECORD,
    "reject_dock_log_submission": CATEGORY_RECORD,
    # Setting the rules the system runs by.
    "add_hub_closure": CATEGORY_POLICY,
    "remove_hub_closure": CATEGORY_POLICY,
    "add_urgency_rule": CATEGORY_POLICY,
    "update_urgency_rule": CATEGORY_POLICY,
    "remove_urgency_rule": CATEGORY_POLICY,
    "approve_proposed_rule": CATEGORY_POLICY,
    "dismiss_proposed_rule_endpoint": CATEGORY_POLICY,
    # Running the business.
    "onboard_client": CATEGORY_BUSINESS,
    "create_hub": CATEGORY_BUSINESS,
    "update_hub": CATEGORY_BUSINESS,
    "onboard_driver": CATEGORY_BUSINESS,
    "admin_issue_sign_in_code": CATEGORY_BUSINESS,
    "admin_revoke_driver_device": CATEGORY_BUSINESS,
    "admin_deactivate_driver": CATEGORY_BUSINESS,
    "admin_reactivate_driver": CATEGORY_BUSINESS,
    "run_payroll_for_hub": CATEGORY_BUSINESS,
    "generate_client_invoice": CATEGORY_BUSINESS,
    "approve_signup": CATEGORY_BUSINESS,
    "reject_signup": CATEGORY_BUSINESS,
    "review_driver_document": CATEGORY_BUSINESS,
    "upsert_client_rate": CATEGORY_BUSINESS,
    "upsert_client_sla_term": CATEGORY_BUSINESS,
}


async def record_ops_touch(request: Request):
    """Router dependency: after an ops write succeeds, record that a person made it."""
    yield
    if request.method not in WRITE_METHODS:
        return
    ops_user_id = _ops_user_id(request.headers.get("authorization"))
    if ops_user_id is None:
        # No ops session: the auth middleware refuses these before the handler
        # runs, so this is a test calling in some other way. Nothing to count.
        return
    route = request.scope.get("route")
    action = getattr(route, "name", None) or request.url.path
    params = {key: str(value) for key, value in request.path_params.items()}
    try:
        async with session_scope() as session:
            order_id = _as_uuid(params.get("order_id"))
            hub_id = _as_uuid(params.get("hub_id")) or await _hub_of(session, params)
            session.add(
                OpsTouch(
                    touched_at=datetime.now(timezone.utc),
                    ops_user_id=ops_user_id,
                    action=action,
                    category=TOUCH_CATEGORIES.get(action, CATEGORY_UNCLASSIFIED),
                    method=request.method,
                    path_params=params,
                    hub_id=hub_id,
                    order_id=order_id,
                )
            )
    except Exception:  # noqa: BLE001 - the write it describes has already happened
        logger.exception("ops_touch_not_recorded", action=action)


async def _hub_of(session: AsyncSession, params: dict[str, str]) -> uuid.UUID | None:
    """The hub a write was about, from whichever thing its path names.

    Without this a per-hub rate counts only writes whose path carries a hub or an
    order, and most of the record work - a stop's label, a return closed, a reply
    to a driver - would vanish from it. A site, a merge or a flag belongs to no
    hub; those stay null and the report counts them beside the hub's figure.
    """
    lookups = (
        ("order_id", select(Order.hub_id).where(Order.id == _as_uuid(params.get("order_id")))),
        ("return_id", select(ReturnItem.hub_id).where(ReturnItem.id == _as_uuid(params.get("return_id")))),
        ("driver_id", select(Driver.hub_id).where(Driver.id == _as_uuid(params.get("driver_id")))),
        ("client_id", select(Client.hub_id).where(Client.id == _as_uuid(params.get("client_id")))),
        (
            "stop_id",
            select(Route.hub_id)
            .join(Stop, Stop.route_id == Route.id)
            .where(Stop.id == _as_uuid(params.get("stop_id"))),
        ),
    )
    for name, query in lookups:
        if _as_uuid(params.get(name)) is not None:
            hub_id = await session.scalar(query)
            if hub_id is not None:
                return hub_id
    return None


def _ops_user_id(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return decode_token(authorization.removeprefix("Bearer ").strip())
    except InvalidOpsToken:
        return None


def _as_uuid(value: str | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
