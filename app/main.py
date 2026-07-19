"""
LMX OS - Phase 1 core backend entrypoint.

Wires together: Order Ingestion Layer, Dynamic SLA Engine, Batch-Hold
Queue, Fleet State Manager, Dispatch Optimizer, and the Annotation and
Learning Loop's nightly job behind a single FastAPI app, per
LMX_OS_Technical_Design_2.md.

NOT in this phase (see docs/ARCHITECTURE.md for the full breakdown):
  - OS Shell (orchestrator/client dashboards, driver mobile app, shop SMS)
  - Twilio / ADP / Gusto wiring
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.driver_routes import router as driver_router
from app.api.routes import router as ops_router
from app.config import settings
from app.db import engine
from app.ingestion.router import router as ingestion_router
from app.logging_config import configure_logging, get_logger
from app.optimizer.event_trigger import dispatch_event_bus
from app.redis_client import close_pool, get_client
from app.security import SharedSecretAuthMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    logger.info("lmx_os_starting")

    # Fail fast on boot if Postgres/Redis aren't reachable, rather than
    # surfacing a confusing error on the first real request.
    async with engine.connect() as conn:
        await conn.run_sync(lambda _: None)
    redis_client = get_client()
    await redis_client.ping()

    logger.info("lmx_os_ready")
    yield

    # Let any event-triggered dispatch cycle in flight finish before the
    # connection pools it depends on go away (app/optimizer/event_trigger.py).
    await dispatch_event_bus.wait_idle()
    await engine.dispose()
    await close_pool()
    logger.info("lmx_os_shutdown")


app = FastAPI(
    title="LMX OS - Phase 1 Core Backend",
    version="0.1.0",
    lifespan=lifespan,
)

# Starlette's add_middleware() inserts at the front of the middleware list,
# so the *last*-added middleware ends up outermost (runs first) - CORS
# must be added after auth, not before, so CORS preflight (OPTIONS) is
# handled before it ever reaches the auth check and gets rejected for
# missing a header no browser sends on a preflight request.
app.add_middleware(SharedSecretAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.dashboard_cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    allow_credentials=False,  # no session/cookie auth exists yet - see docs/ARCHITECTURE.md
)

app.include_router(ops_router)
app.include_router(ingestion_router)
app.include_router(driver_router)
