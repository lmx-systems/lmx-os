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

import uuid

from app.client_auth.login_rate_limit import LoginRateLimiter
from app.client_ip import client_ip
from app.client_auth.password_reset import PasswordResetStore, ResetRequestRateLimitExceeded
from app.client_auth.passwords import hash_password
from app.client_auth.signup_rate_limit import SignupRateLimiter, SignupRateLimitExceeded
from app.config import settings
from app.db import get_db
from app.models.client import Client
from app.models.client_user import CLIENT_ADMIN_ROLE, ClientUser
from app.messaging.client_emails import send_password_reset_email, send_signup_received_email
from app.models.hub import Hub
from app.legal.documents import DOCUMENTS, current_terms_version, documents_are_published
from app.schemas.legal import LegalDocumentBody, LegalDocumentView, LegalDocumentsView
from app.schemas.tracking import DriverPositionView, TrackingView
from app.tracking.rate_limit import TrackingRateLimiter, TrackingRateLimitExceeded
from app.tracking.service import TrackingTokenInvalid, resolve_tracking
from app.schemas.signup import (
    ClientSignupBody,
    ClientSignupResult,
    PasswordResetConfirmBody,
    PasswordResetRequestBody,
    PasswordResetResult,
)

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

    Delegates to the shared helper (L15), which reads X-Forwarded-For according
    to TRUSTED_PROXY_COUNT. This used to be the direct TCP peer, which behind a
    load balancer would have thrown every applicant into one shared bucket - so
    the public signup limit would have been a single budget for the whole
    internet.
    """
    return client_ip(request)


def _document_view(kind: str) -> LegalDocumentView:
    doc = DOCUMENTS[kind]
    return LegalDocumentView(
        kind=doc.kind,
        version=doc.version,
        title=doc.title,
        effective=doc.effective,
        path=doc.portal_path,
        published=doc.is_published,
    )


@router.get("/legal", response_model=LegalDocumentsView)
async def legal_documents() -> LegalDocumentsView:
    """Which terms and privacy policy are current, and whether signup is open.

    The signup form calls this on load instead of holding its own version constant.
    That is the whole point: there is now one place a version is declared, and the
    form presents whatever the server says rather than asserting it.

    Unauthenticated, and deliberately so - this is what has to be readable before
    anyone has an account. It exposes nothing but the identity of two public
    documents.
    """
    return LegalDocumentsView(
        terms=_document_view("terms"),
        privacy=_document_view("privacy"),
        # The form's single question, answered here so the portal never has to
        # reimplement the both-must-be-published rule.
        signup_open=documents_are_published() or settings.allow_unpublished_terms,
    )


@router.get("/legal/{kind}", response_model=LegalDocumentBody)
async def legal_document_body(kind: str) -> LegalDocumentBody:
    """The full text of one document.

    Serves a draft as readily as a published one, with `published` telling the truth
    about which it is. Refusing to serve a draft would leave the portal's /terms page
    blank with no explanation, and a reader who has been told "these are not final"
    is better served than one shown nothing.
    """
    doc = DOCUMENTS.get(kind)
    if doc is None:
        raise HTTPException(status_code=404, detail="No such document")
    return LegalDocumentBody(
        kind=doc.kind,
        version=doc.version,
        title=doc.title,
        effective=doc.effective,
        published=doc.is_published,
        body=doc.body,
    )


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
    # Before the rate limiter, and before anything is written: is there a real
    # document behind the checkbox?
    #
    # Ordering. Everything else in this endpoint is charged to the limiter first so
    # it cannot become an enumeration oracle, but this check leaks nothing about any
    # applicant - it is one global fact, identical for every caller - and spending
    # somebody's signup budget to tell them we are closed would be gratuitous.
    if not documents_are_published():
        if not settings.allow_unpublished_terms:
            logger.error(
                "signup_rejected_terms_unpublished", terms_version=current_terms_version()
            )
            raise HTTPException(
                status_code=503,
                detail="Signups are temporarily unavailable - please try again later",
            )
        # The deliberate escape hatch. Loud, every time, with the version it is
        # recording, so this never becomes the quiet normal state of a deployment.
        logger.warning(
            "signup_accepted_with_unpublished_terms",
            terms_version=current_terms_version(),
            reason="settings.allow_unpublished_terms is on - not for production",
        )

    # Which terms the form was showing. A mismatch means the document changed while
    # this applicant had the page open, so the tick they made was against text they
    # can no longer be said to have accepted. Refuse and make them re-read it.
    #
    # 409 rather than 400: nothing about the submission is malformed, it is stale.
    if body.terms_version != current_terms_version():
        logger.info(
            "signup_rejected_stale_terms",
            submitted=body.terms_version,
            current=current_terms_version(),
        )
        raise HTTPException(
            status_code=409,
            detail="Our terms have been updated. Please reload the page and read them again.",
        )

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
        # The server's version, never the caller's. Checked for equality above, so
        # these agree - but the value written to the evidence column comes from the
        # document, not from the request body.
        terms_accepted_version=current_terms_version(),
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
        terms_version=current_terms_version(),
    )

    # After the commit, so a mail failure can't roll back a real application -
    # and only on the genuinely-new path, never for a duplicate (see the
    # IntegrityError branch above and the note in client_emails.py on why
    # mailing the address's real owner would be a disclosure).
    await send_signup_received_email(
        to=body.contact_email, contact_name=body.contact_name, company_name=body.company_name
    )

    return ClientSignupResult(status="pending", message=_ACCEPTED_MESSAGE)


# ---------------------------------------------------------------------------
# Password reset (docs/ROADMAP.md L14)
#
# Unauthenticated because a locked-out user has no session by definition. Both
# endpoints answer identically whether or not the address is real - see
# PasswordResetResult - so neither can be used to test which companies are LMX
# clients.
# ---------------------------------------------------------------------------

_RESET_REQUESTED_MESSAGE = (
    "If that address has an LMX account, we've sent a link to reset the password. "
    "It's valid for one hour."
)


@router.post("/password-reset/request", response_model=PasswordResetResult, status_code=202)
async def request_password_reset(
    body: PasswordResetRequestBody,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> PasswordResetResult:
    """Ask for a reset link.

    Every branch below returns the same 202 and the same sentence. An unknown
    address, a pending applicant, a deactivated user and a successful send are
    indistinguishable from outside - which is the whole point, because the
    alternative is an endpoint that tells anyone which businesses are LMX
    clients.

    Two throttles, doing different jobs: the IP limiter stops a scripted sweep
    across many addresses, and the per-email limiter stops one inbox being buried
    from many IPs. The per-email charge happens BEFORE the account lookup so it
    applies identically to real and unknown addresses.
    """
    try:
        await SignupRateLimiter().check_and_increment(_client_ip(request))
    except SignupRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    store = PasswordResetStore()
    try:
        await store.check_request_allowed(body.email)
    except ResetRequestRateLimitExceeded:
        # Deliberately NOT a 429. A distinct response here would leak that this
        # address has been asked about repeatedly, which is itself a signal about
        # whether it exists. Silently do nothing and return the same message.
        logger.info("password_reset_rate_limited")
        return PasswordResetResult(message=_RESET_REQUESTED_MESSAGE)

    result = await session.execute(select(ClientUser).where(ClientUser.email == body.email))
    user = result.scalar_one_or_none()

    # Only an ACTIVE user gets a link. A pending applicant would otherwise have
    # their application confirmed to whoever typed the address - and resetting
    # would grant them nothing anyway, since C4 re-checks is_active every request.
    if user is not None and user.is_active:
        token = await store.issue(str(user.id))
        reset_url = f"{settings.portal_base_url.rstrip('/')}/reset-password?token={token}"
        await send_password_reset_email(
            to=user.email, contact_name=user.name, reset_url=reset_url
        )
        logger.info("password_reset_requested", client_user_id=str(user.id))
    else:
        # Logged without the address, so the log isn't the oracle the response
        # refuses to be.
        logger.info("password_reset_requested_for_unusable_account")

    return PasswordResetResult(message=_RESET_REQUESTED_MESSAGE)


@router.post("/password-reset/confirm", response_model=PasswordResetResult)
async def confirm_password_reset(
    body: PasswordResetConfirmBody,
    session: AsyncSession = Depends(get_db),
) -> PasswordResetResult:
    """Redeem a reset link and set a new password.

    A wrong token, an expired one and an already-used one all produce the same
    400. Distinguishing them would tell someone holding a stale link whether it
    was ever valid, and there is nothing a legitimate user does differently with
    that information - they request another link either way.
    """
    store = PasswordResetStore()
    client_user_id = await store.consume(body.token)
    if client_user_id is None:
        raise HTTPException(
            status_code=400,
            detail="That reset link has expired or already been used - please request a new one.",
        )

    user = await session.get(ClientUser, uuid.UUID(client_user_id))
    # Deactivated between the link being issued and used. Rare, but the token
    # outlives the state it was issued against, so it has to be rechecked.
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=400,
            detail="That reset link has expired or already been used - please request a new one.",
        )

    user.password_hash = hash_password(body.new_password)
    await session.commit()

    # They were most likely locked out by failed attempts on the way here, so
    # clear that counter - otherwise a correct new password still bounces.
    await LoginRateLimiter().reset(user.email)
    await store.invalidate_all_for_user(client_user_id)

    logger.info("password_reset_completed", client_user_id=client_user_id)
    return PasswordResetResult(
        message="Your password has been changed - you can sign in with it now."
    )


@router.get("/track/{token}", response_model=TrackingView)
async def track_delivery(
    token: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> TrackingView:
    """Where is my delivery (docs/ROADMAP.md F3).

    The one unauthenticated READ on this router, and the trade-off is the mirror
    image of signup's. Signup's question is "what can a stranger create"; this
    one's is "what can a stranger see, about whom, and for how long" - because the
    obvious implementation hands a member of the public a live GPS feed for one of
    our employees. `app/tracking/service.py` holds the three rules that answer
    that, and `app/schemas/tracking.py` is the exhaustive list of what comes back.

    **404 for an unknown token and for an expired one, with an identical body.**
    Distinguishing them would confirm to a token-guesser that they had found a
    real order, leaving the rate limiter as the only thing between them and a
    working guess. Same reasoning as the uniform responses on signup and password
    reset.
    """
    limiter = TrackingRateLimiter()
    try:
        # Charged before the lookup, so probing tokens costs the prober rather
        # than our database.
        await limiter.check_and_increment(_client_ip(request))
    except TrackingRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    try:
        view = await resolve_tracking(session, token)
    except TrackingTokenInvalid:
        raise HTTPException(
            status_code=404, detail="We couldn't find that delivery"
        ) from None

    return TrackingView(
        status=view.status,
        headline=view.headline,
        detail=view.detail,
        destination_hint=view.destination_hint,
        estimated_arrival=view.estimated_arrival,
        delivered_at=view.delivered_at,
        driver_position=(
            DriverPositionView(
                lat=view.driver_position.lat,
                lng=view.driver_position.lng,
                recorded_at=view.driver_position.recorded_at,
            )
            if view.driver_position is not None
            else None
        ),
        is_live=view.is_live,
    )
