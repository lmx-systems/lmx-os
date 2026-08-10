"""
Sending the recipient their tracking link (docs/ROADMAP.md F3).

**Why pickup is the trigger, and the only one.** F3 is "a public link sent to the
actual delivery recipient, not the shop" - so the link has to reach a person, and
the moment it becomes worth opening is when the parts are on a van. Sent earlier it
shows "scheduling" for an hour, which trains people not to click it; sent on
dispatch it would go out for orders that then sit in a queue.

One message per delivery, on purpose. An SMS per status change would be three or
four texts for one part, which is how a useful channel becomes a muted one - and
the page itself is live, so the link is the update.

Reuses the same `Message` row + `SmsClient` infrastructure as the shop
notifications next door, with `channel="recipient"`. `Message.channel` is a plain
String(16) so this needs no schema change - but it IS a new counterparty class:
these go to a member of the public rather than to a business we have a contract
with, which is why the copy identifies LMX and says what the link does before
showing it.

Best-effort, like every other send in this codebase: called after the
stop-completion commit, and a failure here must never unwind a delivery the driver
has already made.
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.sms_client import get_sms_client
from app.models.message import Message
from app.models.order import Order
from app.tracking.service import ensure_tracking_token, tracking_url

logger = structlog.get_logger(__name__)

_PICKED_UP_TEMPLATE = (
    "Your LMX delivery is on the way. Track it live: {url}"
)
_PICKED_UP_HOT_SHOT_TEMPLATE = (
    "Your LMX Hot Shot delivery is on the way now, direct with no other stops. "
    "Track it live: {url}"
)


def _body(url: str, *, is_hot_shot: bool) -> str:
    template = _PICKED_UP_HOT_SHOT_TEMPLATE if is_hot_shot else _PICKED_UP_TEMPLATE
    return template.format(url=url)


async def notify_recipient_picked_up(
    session: AsyncSession,
    *,
    hub_id: uuid.UUID,
    driver_id: uuid.UUID,
    stop_id: uuid.UUID,
    order: Order,
) -> None:
    """Text the recipient their tracking link, if we have a number for them.

    Returns quietly when there is no `delivery_contact_phone`. That is the common
    case for orders from source systems that never captured one, and an order
    without a recipient phone is not an error - it is a delivery the shop will
    field questions about themselves, exactly as before this feature existed.

    **Minting the token here rather than at ingestion means it is created at the
    moment it is first disclosed.** An order nobody ever texts about never gets a
    tracking credential, which is one fewer live capability sitting in the
    database.
    """
    if not order.delivery_contact_phone:
        return

    token = await ensure_tracking_token(session, order)
    body = _body(tracking_url(token), is_hot_shot=order.sla_tier == "HOT_SHOT")

    try:
        twilio_sid = await get_sms_client().send(order.delivery_contact_phone, body)
    except Exception:  # noqa: BLE001
        # SmsClient.send documents a None return for a failed send, but this is
        # called on the delivery-completion path and a mailer/SMS client that
        # raises must not take a completed pickup down with it. The same
        # assumption bit app/messaging/client_emails.py once already.
        logger.exception("recipient_tracking_sms_failed", order_id=str(order.id))
        twilio_sid = None

    session.add(
        Message(
            hub_id=hub_id,
            driver_id=driver_id,
            stop_id=stop_id,
            channel="recipient",
            direction="outbound",
            body=body,
            counterparty_phone=order.delivery_contact_phone,
            twilio_sid=twilio_sid,
        )
    )
