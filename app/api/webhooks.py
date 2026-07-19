"""
Inbound webhooks - screens 1p/1q's messaging reply path
(app/models/message.py, app/messaging/sms_client.py).

Exempt from both SharedSecretAuthMiddleware (app/security.py's
EXEMPT_PREFIXES) and driver JWT auth - Twilio calls this directly and
carries neither. Real Twilio request-signature validation
(X-Twilio-Signature, verified with TWILIO_AUTH_TOKEN) is NOT implemented
here yet - there's no live Twilio account to test it against in this pass
(see docs/NEXT_STEPS.md) - so this endpoint currently trusts whatever
posts to it. That's a real gap to close before this ever points at a
production Twilio number, not a stylistic choice.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.logging_config import get_logger
from app.models.message import Message

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)

_EMPTY_TWIML = "<Response></Response>"  # empty = "don't auto-reply"


@router.post("/twilio/inbound-sms")
async def twilio_inbound_sms(
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str | None = Form(None),
    session: AsyncSession = Depends(get_db),
) -> Response:
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
