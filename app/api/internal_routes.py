"""
Endpoints a scheduler can call (docs/ROADMAP.md; Cloud Run deployment).

**Why these exist.** Dispatch is event-driven: `app/events/bus.py` runs an
in-process poll loop, started at app startup, and `app/optimizer/event_trigger.py`
binds the optimizer to it. That is the right design and it depends on the process
staying alive between requests - which a serverless platform does not guarantee.
Cloud Run throttles CPU to near-zero between requests and scales to zero when
idle, so the loop can stop running and orders sit in the hold queue with nothing
to release them.

The primary fix is deployment config (CPU always allocated, min-instances 1), not
this. These endpoints are the **safety net**: a low-frequency scheduler ping that
guarantees a cycle happens even if an instance dies, the loop wedges, or someone
changes the CPU setting without knowing what it was holding up. Running a cycle
that had nothing to do is cheap; not running one is an order that never moves.

**Why they aren't just the existing ops endpoints.** `POST /optimizer/{hub}/run-cycle`
already does this, but it sits behind the ops-user JWT middleware
(`app/ops_auth/`), and a scheduler carries a platform OIDC token or a static
secret - not an LMX ops session. Rather than weaken that middleware or mint a
long-lived ops account for a robot, this router has its own shared-secret check.

**Why per-hub isn't the shape.** Each endpoint iterates every active hub, so the
scheduler configuration doesn't hardcode a hub id and a newly onboarded hub is
covered the moment it exists rather than when someone remembers to add a job.

SECURITY: the secret is compared with `secrets.compare_digest`, and the router is
disabled entirely when unset - it does NOT fall open. An unauthenticated
run-cycle endpoint would let anyone force dispatch cycles, which is both a
denial-of-service lever and a way to move real work.
"""
from __future__ import annotations

import secrets as secrets_mod

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.learning_loop.service import run_nightly_job
from app.models.hub import Hub
from app.optimizer.service import DispatchOptimizerService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


async def require_internal_secret(
    x_lmx_internal_token: str | None = Header(default=None),
) -> None:
    """Gate every route on this router.

    Fails closed when no secret is configured: without this the endpoints would
    be wide open on any deployment that forgot to set one, and "forgot to set a
    secret" is exactly the deployment where that matters most.
    """
    expected = settings.internal_api_token
    if not expected:
        raise HTTPException(
            status_code=404,
            detail="Not found",
        )
    if not x_lmx_internal_token or not secrets_mod.compare_digest(
        x_lmx_internal_token, expected
    ):
        # 404 rather than 401, so an unauthenticated prober can't even confirm
        # these routes exist. Nothing legitimate discovers them by probing.
        raise HTTPException(status_code=404, detail="Not found")


async def _active_hub_ids(session: AsyncSession) -> list[str]:
    result = await session.execute(select(Hub.id).where(Hub.active.is_(True)))
    return [str(hub_id) for hub_id in result.scalars().all()]


@router.post("/dispatch/run-all", dependencies=[Depends(require_internal_secret)])
async def run_dispatch_for_all_hubs(session: AsyncSession = Depends(get_db)) -> dict:
    """Run one dispatch cycle per active hub.

    Idempotent and safe to over-call: a cycle with nothing to assign is a cheap
    read. That is what makes this usable as a safety net rather than something
    whose frequency has to be tuned carefully.

    One hub failing must not stop the others - a bad hub's data shouldn't strand
    every other hub's orders - so each is caught and reported.
    """
    results: dict[str, int | str] = {}
    for hub_id in await _active_hub_ids(session):
        try:
            outcome = await DispatchOptimizerService().run_cycle(hub_id)
            results[hub_id] = len(outcome.assignments)
        except Exception as exc:  # noqa: BLE001 - one hub must not stop the rest
            logger.exception("scheduled_dispatch_failed", hub_id=hub_id)
            results[hub_id] = f"error: {type(exc).__name__}"
    logger.info("scheduled_dispatch_complete", hubs=len(results))
    return {"hubs": results}


@router.post("/learning-loop/run-all", dependencies=[Depends(require_internal_secret)])
async def run_learning_loop_for_all_hubs(session: AsyncSession = Depends(get_db)) -> dict:
    """Run the Learning Loop's nightly pattern detection for every active hub.

    The in-process scheduler runs this at each hub's own local 2am, which is the
    behaviour worth keeping. This exists for the same reason as the dispatch route
    above - a suspended process runs no scheduler - and is safe to call more than
    once a day: the job proposes rules from patterns it detects, and re-detecting
    the same pattern does not double-propose.
    """
    results: dict[str, int | str] = {}
    for hub_id in await _active_hub_ids(session):
        try:
            # Returns the ProposedRule rows created, not a result object.
            created = await run_nightly_job(session, hub_id=hub_id)
            results[hub_id] = len(created)
        except Exception as exc:  # noqa: BLE001
            logger.exception("scheduled_learning_loop_failed", hub_id=hub_id)
            results[hub_id] = f"error: {type(exc).__name__}"
    logger.info("scheduled_learning_loop_complete", hubs=len(results))
    return {"hubs": results}
