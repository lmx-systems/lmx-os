"""
Real PIN issuance for proof-of-delivery (docs/ROADMAP.md A4) - a delivery
PIN is generated the moment a dropoff stop is created (accept_offer,
app/api/driver_routes.py) and texted to the customer, then checked
server-side against what the driver actually submits at complete_stop
time (Stop.pod_pin vs. Stop.delivery_pin) - not just recorded, closing
the gap Stop.delivery_pin's own docstring names.

Same masked-SMS mechanics as app/api/driver_routes.py's message_customer
(the driver never sees the customer's real phone number either way,
since this is an automated server-side send, not something a driver
composes) but its own channel, not "customer" - that channel is
specifically the driver-composed conversation thread
(list_customer_messages), and this system notification has no business
appearing inside it, the same reasoning that already gives shop
notifications their own channel="shop" in
app/messaging/shop_notifications.py.
"""
from __future__ import annotations

import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.sms_client import get_sms_client
from app.models.message import Message
from app.models.order import Order
from app.models.stop import Stop

PIN_LENGTH = 4
MAX_PIN_VERIFICATION_ATTEMPTS = 5


def generate_delivery_pin() -> str:
    return f"{secrets.randbelow(10 ** PIN_LENGTH):0{PIN_LENGTH}d}"


async def send_delivery_pin_sms(
    session: AsyncSession, *, hub_id: uuid.UUID, driver_id: uuid.UUID, stop: Stop, order: Order
) -> None:
    """Call only after stop.delivery_pin has already been set and
    committed (see accept_offer) - this just sends the text and records
    it, best-effort, same as the shop-notification sends elsewhere in
    this module family: a failed send here must never roll back or block
    the route the driver already accepted."""
    if not order.delivery_contact_phone or not stop.delivery_pin:
        return

    body = f"Your LMX delivery is on the way. Give this code to your driver to confirm delivery: {stop.delivery_pin}"
    twilio_sid = await get_sms_client().send(order.delivery_contact_phone, body)

    session.add(
        Message(
            hub_id=hub_id,
            driver_id=driver_id,
            stop_id=stop.id,
            channel="delivery_pin",
            direction="outbound",
            body=body,
            counterparty_phone=order.delivery_contact_phone,
            twilio_sid=twilio_sid,
        )
    )
