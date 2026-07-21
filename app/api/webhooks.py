"""
Inbound webhooks - screens 1p/1q's messaging reply path
(app/models/message.py, app/messaging/sms_client.py).

Exempt from both SharedSecretAuthMiddleware (app/security.py's
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.logging_config import get_logger
from app.messaging.twilio_signature import signature_is_valid
from app.models.message import Message

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)

_EMPTY_TWIML = "<Response></Response>"  # empty = "don't auto-reply"


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

    # Match against the most recent outbound message sent to this number,
    # to figure out which driver/channel/stop the reply belongs to - there's
    # no session/proxy concept here, just phone-number matching. Real
    # limitation: if a driver has two concurrent conversations with the
    # same number (e.g. messaged the same customer about two different
    # stops), this attaches the reply to whichever is more recent, not
    # necessarily the right one.
    result = await session.execute(
        select(Message)
        .where(Message.counterparty_phone == From, Message.direction == "outbound")
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    most_recent = result.scalar_one_or_none()

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
