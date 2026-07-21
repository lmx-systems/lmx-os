"""
Inbound webhooks - screens 1p/1q's messaging reply path
(app/models/message.py, app/messaging/sms_client.py).

Exempt from both SharedSecretAuthMiddleware (app/security.py's
EXEMPT_PREFIXES) and driver JWT auth - Twilio calls this directly and
carries neither. Instead, authenticity comes from Twilio's own request
signature: when TWILIO_AUTH_TOKEN is configured, every request must carry
a valid X-Twilio-Signature (see app/messaging/twilio_signature.py) or it
is rejected with 403. When no auth token is configured (local dev - the
same "unconfigured -> stub" pattern as the SMS client itself), requests
are accepted unverified, since there's no real Twilio account whose
traffic could be spoofed yet.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.logging_config import get_logger
from app.messaging.twilio_signature import signature_is_valid
from app.models.message import Message
from app.models.stop import Stop

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)

_EMPTY_TWIML = "<Response></Response>"  # empty = "don't auto-reply"

# How far back an inbound reply can match an outbound thread (roadmap item
# A8). A reply to a message older than this is almost certainly not about
# that delivery anymore - better to record it unmatched than to attach it
# to a days-old conversation.
REPLY_MATCH_WINDOW_HOURS = 24


def _verify_twilio_signature(request: Request, form_params: dict[str, str]) -> None:
    """403 unless the request is provably from Twilio (roadmap item S7)."""
    if not settings.twilio_auth_token:
        # No Twilio account configured - nothing real to protect, and no
        # token to verify against. Same stub posture as SmsClient.
        logger.warning("twilio_signature_check_skipped_no_auth_token")
        return

    url = settings.twilio_webhook_public_url or str(request.url)
    provided = request.headers.get("X-Twilio-Signature")
    if not signature_is_valid(settings.twilio_auth_token, url, form_params, provided):
        logger.warning("twilio_signature_invalid")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Twilio signature")


async def _match_reply_to_thread(session: AsyncSession, from_number: str) -> Message | None:
    """
    Decide which conversation an inbound reply belongs to (roadmap item A8).

    Matching is still fundamentally by phone number (one shared Twilio
    number, no per-conversation proxy sessions - that's Twilio Proxy
    territory, a later upgrade). What this hardens over the previous
    "most recent outbound to this number, ever" behavior:

    1. Only outbound messages within REPLY_MATCH_WINDOW_HOURS are
       candidates - a reply can no longer attach to a days-old thread.
    2. If the candidates span more than one conversation (different
       stop/channel), prefer conversations whose stop is still active
       (not completed) - a customer replying is far more likely to be
       talking about the delivery still underway than one already done.
    3. If it's *still* ambiguous (two active conversations with the same
       number), fall back to most-recent and log a structured warning so
       the misattribution risk is visible in logs instead of silent.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=REPLY_MATCH_WINDOW_HOURS)
    result = await session.execute(
        select(Message)
        .where(
            Message.counterparty_phone == from_number,
            Message.direction == "outbound",
            Message.created_at >= cutoff,
        )
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return None

    # One message per distinct conversation, keeping the most recent
    # (candidates are already newest-first).
    threads: dict[tuple, Message] = {}
    for msg in candidates:
        key = (str(msg.driver_id), msg.channel, str(msg.stop_id) if msg.stop_id else None)
        threads.setdefault(key, msg)

    if len(threads) == 1:
        return next(iter(threads.values()))

    # Multiple conversations with this number - prefer ones whose stop is
    # still active. Support threads (stop_id None) count as active too.
    stop_ids = [msg.stop_id for msg in threads.values() if msg.stop_id is not None]
    completed_stop_ids: set = set()
    if stop_ids:
        stops_result = await session.execute(
            select(Stop.id).where(Stop.id.in_(stop_ids), Stop.status == "completed")
        )
        completed_stop_ids = {row[0] for row in stops_result.all()}

    active = [
        msg for msg in threads.values()
        if msg.stop_id is None or msg.stop_id not in completed_stop_ids
    ]
    pool = active if active else list(threads.values())
    if len(pool) > 1:
        logger.warning(
            "inbound_sms_ambiguous_match",
            candidate_thread_count=len(threads),
            active_thread_count=len(active),
        )
    return max(pool, key=lambda m: m.created_at)


@router.post("/twilio/inbound-sms")
async def twilio_inbound_sms(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    # The signature covers every form param Twilio sent, not just the ones
    # this handler uses - so parse the whole form first, verify, then pick
    # out the fields.
    form = await request.form()
    form_params = {key: str(value) for key, value in form.items()}
    _verify_twilio_signature(request, form_params)

    from_number = form_params.get("From")
    body = form_params.get("Body")
    message_sid = form_params.get("MessageSid")
    if not from_number or body is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Missing From/Body"
        )

    most_recent = await _match_reply_to_thread(session, from_number)

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
                body=body,
                counterparty_phone=from_number,
                twilio_sid=message_sid,
            )
        )
        await session.commit()

    return Response(content=_EMPTY_TWIML, media_type="application/xml")
