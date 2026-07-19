"""
Shop SMS automation (Phase 8, see docs/ROADMAP.md) - one-way notifications
to a shop's Shop.phone, automatically triggered by the driver app rather
than driver-composed (unlike the customer/support messaging in
app/api/driver_routes.py's message_customer/message_support). Reuses the
same Message/SmsClient infrastructure with channel="shop" - no schema
change needed since Message.channel is a plain String(16) column.

Two trigger events, each with a HOT_SHOT-specific variant that conveys the
tier's speed/priority (per Sourabh: "Hot Shot specific messaging that
helps shop know that their order is coming to them fairly quickly"):
  1. "picked up" - the driver just completed this pickup stop.
  2. "en route" - this pickup stop just became the driver's next stop
     (either the first stop right after accepting an offer, or the next
     not-yet-completed pickup right after the previous one is completed).

One-way per Sourabh's explicit call ("one way notification only") - no
reply-handling UI is built for this channel; an inbound reply would still
land in the messages table via the existing Twilio webhook (phone-number
matching doesn't care about channel), it just has nowhere to surface today.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.sms_client import get_sms_client
from app.models.message import Message
from app.models.shop import Shop

# Every shop SMS ends with this - Sourabh's call, for a bit of brand fun on
# an otherwise purely operational notification.
SIGN_OFF = "Thanks for LMX'ing it!"

_PICKED_UP_TEMPLATE = (
    "Hi {shop_name}, your LMX driver just picked up your order and is on the way to the customer. " + SIGN_OFF
)
_PICKED_UP_HOT_SHOT_TEMPLATE = (
    "Hi {shop_name}, your Hot Shot order is picked up and headed straight to the customer now - "
    "no other stops in between. " + SIGN_OFF
)
_EN_ROUTE_TEMPLATE = (
    "Hi {shop_name}, your LMX driver is on the way to pick up your order now. " + SIGN_OFF
)
_EN_ROUTE_HOT_SHOT_TEMPLATE = (
    "Hi {shop_name}, heads up - a driver is en route now for a priority Hot Shot pickup. " + SIGN_OFF
)


def _picked_up_body(shop_name: str, is_hot_shot: bool) -> str:
    template = _PICKED_UP_HOT_SHOT_TEMPLATE if is_hot_shot else _PICKED_UP_TEMPLATE
    return template.format(shop_name=shop_name)


def _en_route_body(shop_name: str, is_hot_shot: bool) -> str:
    template = _EN_ROUTE_HOT_SHOT_TEMPLATE if is_hot_shot else _EN_ROUTE_TEMPLATE
    return template.format(shop_name=shop_name)


async def _send_shop_sms(
    session: AsyncSession, *, hub_id: uuid.UUID, driver_id: uuid.UUID, stop_id: uuid.UUID, shop: Shop, body: str
) -> None:
    if not shop.phone:
        # Same "no destination configured -> store, don't pretend to send"
        # pattern as message_support in app/api/driver_routes.py - a shop
        # without a phone on file shouldn't block the pickup/complete flow.
        twilio_sid = None
    else:
        twilio_sid = await get_sms_client().send(shop.phone, body)

    session.add(
        Message(
            hub_id=hub_id,
            driver_id=driver_id,
            stop_id=stop_id,
            channel="shop",
            direction="outbound",
            body=body,
            counterparty_phone=shop.phone,
            twilio_sid=twilio_sid,
        )
    )


async def notify_shop_picked_up(
    session: AsyncSession, *, hub_id: uuid.UUID, driver_id: uuid.UUID, stop_id: uuid.UUID, shop: Shop, is_hot_shot: bool
) -> None:
    await _send_shop_sms(
        session, hub_id=hub_id, driver_id=driver_id, stop_id=stop_id, shop=shop,
        body=_picked_up_body(shop.name, is_hot_shot),
    )


async def notify_shop_en_route(
    session: AsyncSession, *, hub_id: uuid.UUID, driver_id: uuid.UUID, stop_id: uuid.UUID, shop: Shop, is_hot_shot: bool
) -> None:
    await _send_shop_sms(
        session, hub_id=hub_id, driver_id=driver_id, stop_id=stop_id, shop=shop,
        body=_en_route_body(shop.name, is_hot_shot),
    )
