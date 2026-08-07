"""
The only unauthenticated write surface in this application
(docs/LMX_LINK_PLAN.md).

Everything else here is behind driver auth, client auth or ops auth. This
endpoint is reachable by anyone on the internet and creates rows, which makes it
worth being explicit about what protects it:

  - **Rate limited by IP before anything else happens**, including before the
    duplicate-email check. That ordering is deliberate and matches what the S6
    security pass did to driver OTP issuance: charging the limiter afterwards
    would leave this as an enumeration oracle, letting an attacker discover which
    companies already have an account by watching for conflict responses.
  - **Creates nothing that can act.** The client lands in `pending` and its first
    user is created inactive, so C4's existing per-request `is_active` check
    already prevents login. No new state in the auth path, and no window where a
    self-signed-up stranger can dispatch a van.
  - **Says almost nothing back.** No client id, no internal state beyond
    "pending". An unauthenticated caller has no business learning our
    identifiers.

This reverses roadmap item `C5` ("no client-initiated signup, by design"). The
approval gate in `app/api/admin_routes.py` is what preserves the B2B posture that
decision was protecting.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.client_auth.passwords import hash_password
from app.client_auth.signup_rate_limit import SignupRateLimiter, SignupRateLimitExceeded
from app.db import get_db
from app.models.client import Client
from app.models.client_user import CLIENT_ADMIN_ROLE, ClientUser
from app.models.hub import Hub
from app.schemas.signup import ClientSignupBody, ClientSignupResult

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/public", tags=["public"])

# What every applicant is told, whether or not their email was already taken.
# Uniform on purpose - see the duplicate handling below.
_ACCEPTED_MESSAGE = (
    "Thanks - your details are with our team. We'll be in touch once your "
    "account is approved."
)


def _client_ip(request: Request) -> str:
    """The caller's address, for rate limiting.

    Same limitation the S6 pass recorded for `app/rate_limit.py`: this is the
    direct TCP peer. Correct until a real reverse proxy sits in front (Phase 5's
    hosting decision), after which it must read X-Forwarded-For or it will
    throttle the proxy rather than the caller.
    """
    return request.client.host if request.client else "unknown"


@router.post("/signup", response_model=ClientSignupResult, status_code=202)
async def client_signup(
    body: ClientSignupBody,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ClientSignupResult:
    """Apply for an LMX account.

    202 rather than 201: this accepted an application, it did not create
    something the caller can now use. Nothing is usable until a human approves.
    """
    limiter = SignupRateLimiter()
    try:
        await limiter.check_and_increment(_client_ip(request))
    except SignupRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    # Provisional hub. Hubs have no service-area model, so a signup cannot be
    # routed automatically - ops assigns the real one at approval, reading
    # `service_area`. Earliest hub by creation, which at pilot scale is the only
    # hub. Harmless because a pending client cannot do anything with it.
    hub = (
        await session.execute(select(Hub).order_by(Hub.created_at).limit(1))
    ).scalar_one_or_none()
    if hub is None:
        # No hubs configured at all - a deployment problem, not the applicant's.
        # Deliberately not leaked as such.
        logger.error("signup_rejected_no_hub")
        raise HTTPException(
            status_code=503, detail="Signups are temporarily unavailable - please try again later"
        )

    now = datetime.now(timezone.utc)
    client = Client(
        hub_id=hub.id,
        name=body.company_name,
        pos_system="client_portal",
        signup_status="pending",
        service_area=body.service_area,
        contact_phone=body.contact_phone,
        terms_accepted_version=body.terms_version,
        terms_accepted_at=now,
    )
    session.add(client)
    await session.flush()

    session.add(
        ClientUser(
            client_id=client.id,
            email=body.contact_email,
            password_hash=hash_password(body.password),
            name=body.contact_name,
            role=CLIENT_ADMIN_ROLE,
            # THE GATE. C4 re-checks is_active on every request
            # (app/client_auth/dependencies.py), so this alone prevents login
            # until approval - no new state in the auth path.
            is_active=False,
        )
    )

    try:
        await session.commit()
    except IntegrityError:
        # ClientUser.email is globally unique. Somebody already signed up with
        # this address - possibly this same applicant, twice.
        #
        # Answered with the SAME 202 and the same message as a fresh signup, on
        # purpose: a distinct 409 would turn this endpoint into a way to test
        # whether a given company or person is already an LMX customer, which is
        # exactly the disclosure the pre-charged rate limiter above is there to
        # make expensive. Logged so ops can see it happened.
        await session.rollback()
        logger.info("signup_duplicate_email_ignored", company=body.company_name)
        return ClientSignupResult(status="pending", message=_ACCEPTED_MESSAGE)

    logger.info(
        "client_signup_received",
        client_id=str(client.id),
        company=body.company_name,
        service_area=body.service_area,
        terms_version=body.terms_version,
    )
    return ClientSignupResult(status="pending", message=_ACCEPTED_MESSAGE)
