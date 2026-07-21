"""
Structured logging setup (structlog -> JSON in prod, console in dev),
plus error tracking via Sentry (docs/ROADMAP.md S4) - right now the only
way to know something broke is someone noticing.

Sentry's own FastAPI/Starlette integrations only capture *unhandled*
exceptions that propagate all the way up through the ASGI stack - they
never see an exception this codebase deliberately catches and logs
without re-raising (e.g. app/events/bus.py's HubEventBus, which must
never let one hub's handler failure crash the poll loop or take down
another hub's run). _forward_to_sentry below closes that gap: since this
file's structlog setup uses PrintLoggerFactory (writes straight to
stdout, never touches Python's stdlib logging module), Sentry's default
LoggingIntegration - which hooks stdlib logging - would never see any of
these either, so this forwards warning/error/critical/exception-level
structlog events to Sentry directly instead of relying on that hook.
"""
import logging
import sys

import sentry_sdk
import structlog
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from app.config import settings

_SENTRY_LOG_METHODS = {"warning", "error", "critical", "exception"}


def _forward_to_sentry(logger, method_name, event_dict):
    """A structlog processor (not a renderer) - returns event_dict
    unchanged so the pipeline continues on to the actual renderer; this
    only has a side effect (sending to Sentry), it never alters what gets
    logged locally. Sentry's own capture_* calls are already safe no-ops
    when sentry_sdk.init() was never called (see settings.sentry_dsn's
    docstring), so this doesn't need its own separate on/off check.
    """
    if method_name in _SENTRY_LOG_METHODS:
        with sentry_sdk.push_scope() as scope:
            for key, value in event_dict.items():
                if key not in ("event", "level", "timestamp"):
                    scope.set_extra(key, value)
            if method_name == "exception":
                sentry_sdk.capture_exception()
            else:
                sentry_sdk.capture_message(event_dict.get("event", ""), level=method_name)
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level.upper(),
    )

    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            integrations=[StarletteIntegration(), FastApiIntegration()],
        )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    if settings.sentry_dsn:
        shared_processors.append(_forward_to_sentry)

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.environment == "production"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
