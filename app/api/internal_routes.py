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

**Also here: the alerting endpoint** (`GET /health/dispatch`, docs/ALERTING.md).
Not a scheduler trigger, but the same kind of caller - an automated platform poller
holding a shared secret rather than an ops session - and the same gate applies.

SECURITY: the secret is compared with `secrets.compare_digest`, and the router is
disabled entirely when unset - it does NOT fall open. An unauthenticated
run-cycle endpoint would let anyone force dispatch cycles, which is both a
denial-of-service lever and a way to move real work.
"""
from __future__ import annotations

import secrets as secrets_mod
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.client_ip import client_ip
from app.config import settings
from app.db import get_db
from app.health.checks import evaluate
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


@router.get("/health/dispatch", dependencies=[Depends(require_internal_secret)])
async def dispatch_health(response: Response) -> dict:
    """200 when the fleet is fine, 503 when something is worth waking up for.

    **This endpoint IS the alert rule.** A Cloud Monitoring uptime check hitting
    this URL on a timer, with an alert policy on check failure, is the whole
    alerting stack - no Prometheus server, no sidecar collector, no time-series
    database. `app/health/checks.py` explains why that is the right shape here
    rather than a shortcut: per-process metrics cannot answer a question about an
    autoscaled service, and the condition that matters most ("dispatch stopped")
    is an absence rather than a value.

    Deliberately NOT the existing `GET /health`, which reports that this process
    can serve a request and is what a load balancer should use. Mixing them would
    mean a wedged dispatch loop pulls the instance out of service, which fixes
    nothing and takes the API down with it.

    Behind the internal token like everything else on this router: hub ids, queue
    depths and late-order counts are operational intelligence. Cloud Monitoring
    uptime checks can send a custom header, so this costs nothing to reach.

    **The response body is written for whoever the alert wakes.** The status code
    fires the alert; `failing` and each check's `detail` say what broke and
    against which threshold, so the first move is obvious without opening the
    codebase.
    """
    report = await evaluate()
    if not report.ok:
        # 503 rather than 500: this instance is serving fine, the system it
        # watches is not. Any non-200 trips the uptime check either way, but the
        # distinction matters when reading logs after the fact.
        response.status_code = 503
    return {
        "status": "ok" if report.ok else "degraded",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "failing": report.failing,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in report.checks
        ],
    }


@router.get("/forwarded-headers", dependencies=[Depends(require_internal_secret)])
async def inspect_forwarded_headers(request: Request) -> dict:
    """What the proxy chain actually looks like, for setting TRUSTED_PROXY_COUNT.

    Exists because that setting cannot be guessed and getting it wrong in the
    too-high direction is worse than leaving it at 0: each proxy APPENDS the
    address it saw, so claiming more proxies than exist starts trusting entries
    the caller wrote, and an attacker can then mint a fresh empty rate-limit
    bucket per request. Every platform orders and rewrites `X-Forwarded-For`
    slightly differently, so the only reliable answer comes from a real request
    through the real infrastructure.

    **How to use it.** Call this from a network whose public IP you know - a
    phone on mobile data is easiest - and find your own address in `chain`. The
    `trusted_proxy_count_if_this_is_you` on that entry is the value to set.

    Behind the internal token, not open: echoing request headers back is an
    information-disclosure surface, and it reveals internal proxy addresses.

    Safe to leave in place. It stays useful for diagnosing rate-limiting that is
    throttling the wrong thing, which is a class of problem that is otherwise
    very hard to see from the outside.
    """
    forwarded = request.headers.get("x-forwarded-for")
    entries = [part.strip() for part in (forwarded or "").split(",") if part.strip()]

    # Repeated headers are a real case and they change how the chain parses -
    # some infrastructure appends a second X-Forwarded-For rather than extending
    # the first, and Starlette joins those with ", " when you read one value.
    # Surfaced separately so that doesn't quietly look like one long chain.
    raw_occurrences = request.headers.getlist("x-forwarded-for")

    return {
        "resolved_client_ip": client_ip(request),
        "configured_trusted_proxy_count": settings.trusted_proxy_count,
        "tcp_peer": request.client.host if request.client else None,
        "x_forwarded_for_raw": forwarded,
        "x_forwarded_for_header_count": len(raw_occurrences),
        # Rightmost first, because that is the end written by our own
        # infrastructure and the end the count is measured from.
        "chain": [
            {
                "value": value,
                "position_from_right": index + 1,
                "trusted_proxy_count_if_this_is_you": index + 1,
            }
            for index, value in enumerate(reversed(entries))
        ],
        "x_forwarded_proto": request.headers.get("x-forwarded-proto"),
        "forwarded": request.headers.get("forwarded"),
        "how_to_read_this": (
            "Find your own public IP in `chain` and set TRUSTED_PROXY_COUNT to "
            "that entry's trusted_proxy_count_if_this_is_you. If your IP is not "
            "in the chain at all, leave it at 0 - something is rewriting the "
            "header and the TCP peer is the only honest value."
        ),
    }
