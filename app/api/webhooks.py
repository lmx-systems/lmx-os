"""
Inbound webhooks - screens 1p/1q's messaging reply path
(app/models/message.py, app/messaging/sms_client.py).

Exempt from both OpsUserAuthMiddleware (app/ops_auth/middleware.py's
EXEMPT_PREFIXES) and driver JWT auth - Twilio calls this directly and
carries neither. Request-signature verification
(app/messaging/twilio_signature.py) only actually enforces when
TWILIO_AUTH_TOKEN is configured - same "unconfigured credential -> trust/
stub" pattern as app/messaging/sms_client.py, since there's no live
Twilio account to validate a real signature against otherwise (see
docs/NEXT_STEPS.md), and enforcing this in that state would just break
local dev/tests rather than add real security.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.driver_routes import _TERMINAL_STOP_STATUSES
from app.config import settings
from app.db import get_db
from app.logging_config import get_logger
from app.messaging.twilio_signature import signature_is_valid
from app.models.message import Message
from app.models.stop import Stop

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)

_EMPTY_TWIML = "<Response></Response>"  # empty = "don't auto-reply"


async def _find_matching_thread(session: AsyncSession, from_number: str) -> Message | None:
    """
    Which outbound message this inbound reply belongs to. Real, honest
    limitation up front: if two conversations to the same counterparty
    number are genuinely BOTH still open at once (two drivers with
    concurrent unanswered support threads - every driver's support
    messages share the exact same counterparty_phone,
    settings.support_phone_number - or one driver messaging the same
    customer number about two different still-active stops), there is no
    way to tell them apart from phone number alone. Solving that for real
    needs either a Twilio Proxy-style number-per-conversation pool or an
    explicit reference code in the reply body, neither of which exists
    here. What this narrows, without pretending to solve that:

      1. Channel is inferred, not assumed - a `From` matching
         settings.support_phone_number is unambiguously a support reply,
         never a customer one (or vice versa). The prior version matched
         across channels and drivers with no distinction at all - a
         support reply could in principle land in another driver's
         customer thread just because the phone numbers happened to
         match "most recently."
      2. Only threads with no inbound reply already recorded since their
         last outbound message count as "still open" - an
         already-answered conversation shouldn't keep absorbing replies
         meant for a newer one to the same number.
      3. Customer-channel threads additionally require their stop to
         still be non-terminal - a completed/failed stop's conversation
         is over and shouldn't still be a match candidate.

    Ambiguity that survives all three (genuinely concurrent, still-open
    threads to the same number) is logged as such, not silently guessed
    at - and this is precisely the case a real fix would need new
    infrastructure for, not more matching logic.
    """
    is_support_reply = bool(settings.support_phone_number) and from_number == settings.support_phone_number
    channel = "support" if is_support_reply else "customer"

    candidates_result = await session.execute(
        select(Message)
        .where(Message.counterparty_phone == from_number, Message.direction == "outbound", Message.channel == channel)
        .order_by(Message.created_at.desc())
    )
    candidates = list(candidates_result.scalars().all())
    if not candidates:
        return None

    # One row per thread - (driver_id, stop_id) for customer (per-stop
    # conversations), driver_id alone for support (one ongoing thread per
    # driver, stop_id is always null there). Already sorted desc, so the
    # first one seen per key is that thread's most recent outbound message.
    latest_per_thread: dict[tuple, Message] = {}
    for message in candidates:
        key = (message.driver_id, message.stop_id)
        latest_per_thread.setdefault(key, message)

    open_threads = []
    for message in latest_per_thread.values():
        replied_already = await session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.driver_id == message.driver_id,
                Message.stop_id == message.stop_id,
                Message.channel == channel,
                Message.direction == "inbound",
                Message.created_at > message.created_at,
            )
        )
        if replied_already.scalar_one() == 0:
            open_threads.append(message)

    if channel == "customer" and open_threads:
        stop_ids = [m.stop_id for m in open_threads]
        active_stops = await session.execute(
            select(Stop.id).where(Stop.id.in_(stop_ids), Stop.status.notin_(_TERMINAL_STOP_STATUSES))
        )
        active_stop_ids = {row[0] for row in active_stops.all()}
        still_active = [m for m in open_threads if m.stop_id in active_stop_ids]
        if still_active:
            open_threads = still_active

    if not open_threads:
        # Every candidate thread already got a reply (or, for customer
        # channel, every candidate's stop is terminal) - fall back to the
        # single most recent candidate rather than dropping the message
        # entirely, since Twilio doesn't retry cleanly on an unmatched
        # webhook and a human should still see this land somewhere.
        return candidates[0]

    if len(open_threads) > 1:
        logger.warning(
            "inbound_sms_ambiguous_match",
            channel=channel,
            from_number=from_number,
            candidate_count=len(open_threads),
        )

    return max(open_threads, key=lambda m: m.created_at)


def _webhook_url(request: Request) -> str:
    # request.url reflects this container's own view of the request,
    # correct only when nothing sits in front of it - see
    # settings.twilio_webhook_base_url's docstring for the reverse-proxy
    # case, where Twilio actually signed the public URL, not this one.
    if settings.twilio_webhook_base_url:
        url = settings.twilio_webhook_base_url.rstrip("/") + request.url.path
        if request.url.query:
            url += f"?{request.url.query}"
        return url
    return str(request.url)


async def _assert_valid_twilio_signature(request: Request) -> None:
    if not settings.twilio_auth_token:
        return
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}
    signature = request.headers.get("X-Twilio-Signature")
    if not signature_is_valid(settings.twilio_auth_token, _webhook_url(request), params, signature):
        logger.warning("twilio_signature_invalid", path=request.url.path)
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


@router.post("/twilio/inbound-sms")
async def twilio_inbound_sms(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str | None = Form(None),
    session: AsyncSession = Depends(get_db),
) -> Response:
    await _assert_valid_twilio_signature(request)

    most_recent = await _find_matching_thread(session, From)

    if most_recent is None:
        logger.warning("inbound_sms_unmatched")
    else:
        session.add(
            Message(
                hub_id=most_recent.hub_id,
                driver_id=most_recent.driver_id,
                stop_id=most_recent.stop_id,
                channel=most_recent.channel,
                direction="inbound",
                body=Body,
                counterparty_phone=From,
                twilio_sid=MessageSid,
            )
        )
        await session.commit()

    return Response(content=_EMPTY_TWIML, media_type="application/xml")
