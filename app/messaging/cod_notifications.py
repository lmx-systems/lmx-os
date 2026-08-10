"""
Telling the distributor their customer wouldn't pay (docs/ROADMAP.md W2).

The second clause of the rule - *"one tap escalates to the distributor"* - and the whole
value of it is that somebody who can act actually hears. A dispute recorded in a table
nobody is watching is the driver having stood there for nothing.

**Escalates to the SHOP, not to LMX ops.** The money is the distributor's invoice to
their own customer, so they are the only party who can decide anything: waive it, insist
on it, phone the customer, send it again tomorrow. Routing this to LMX ops first would
insert us into a commercial dispute we are not part of, and add a hop that costs the one
thing that matters - the distributor learning about it while their customer is still
standing there.

Deliberately says what was disputed and nothing about who is right. The driver's note is
passed along as the customer's account, because a pattern across an account is the useful
signal and paraphrasing it would lose that.
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.sms_client import get_sms_client
from app.models.message import Message
from app.models.shop import Shop

logger = structlog.get_logger(__name__)

_TEMPLATE = (
    "LMX: your customer at {address} declined to pay the ${amount:.2f} due on order "
    "{reference}. Our driver did not negotiate and has moved on. {note}"
    "Please contact your customer and let us know how to proceed."
)


def _body(*, address: str, amount_cents: int, reference: str, note: str | None) -> str:
    return _TEMPLATE.format(
        address=address or "the delivery address",
        amount=(amount_cents or 0) / 100,
        reference=reference or "(no reference)",
        note=f'They said: "{note}". ' if note else "",
    )


# What happened to the escalation, which is not a boolean.
#
# The stub SMS client returns no Twilio SID by design, so "did a SID come back" would
# report EVERY dispute on today's deployment as un-escalated - a signal that cries wolf
# permanently and stops being read. That is a deployment-wide configuration gap (B5,
# provision a real Twilio account), not a per-dispute failure, and the two want reporting
# at completely different granularities: one banner line versus a count.
ESCALATION_SENT = "sent"
ESCALATION_NO_PHONE = "no_phone_on_file"
ESCALATION_NOT_CONFIGURED = "sms_not_configured"
ESCALATION_FAILED = "send_failed"


def sms_is_configured() -> bool:
    """Whether a real SMS provider exists on this deployment.

    Read by the dispute report so a reader knows whether `unescalated_count` means "these
    distributors were not told" or "nothing is being sent at all yet".
    """
    return get_sms_client().engine_name != "stub"


async def notify_shop_of_cod_dispute(
    session: AsyncSession,
    *,
    hub_id: uuid.UUID,
    driver_id: uuid.UUID,
    stop_id: uuid.UUID,
    shop: Shop | None,
    delivery_address: str | None,
    amount_cents: int,
    reference: str,
    note: str | None,
) -> str:
    """Text the distributor. Returns one of the ESCALATION_* outcomes.

    Only `ESCALATION_SENT` sets `CodCollection.escalated_at`: a dispute nobody was told
    about is a real state, and recording it as escalated anyway would hide the one failure
    that breaks the promise this feature makes.

    Best-effort, like every send in this codebase - a driver who has already left must not
    be blocked by an SMS gateway, and the dispute row is committed either way.
    """
    body = _body(
        address=delivery_address or "",
        amount_cents=amount_cents,
        reference=reference,
        note=note,
    )

    twilio_sid = None
    if shop is None or not shop.phone:
        # Stored rather than pretend-sent, same as the shop notifications next door. A
        # distributor with no number on file is a gap ops can see and close.
        logger.warning(
            "cod_dispute_not_escalated",
            stop_id=str(stop_id),
            reason=ESCALATION_NO_PHONE,
            detail="no shop phone on file - the distributor has not been told",
        )
        outcome = ESCALATION_NO_PHONE
    elif not sms_is_configured():
        logger.warning(
            "cod_dispute_not_escalated",
            stop_id=str(stop_id),
            reason=ESCALATION_NOT_CONFIGURED,
            detail="no SMS provider on this deployment (B5) - nothing was actually sent",
        )
        outcome = ESCALATION_NOT_CONFIGURED
    else:
        try:
            twilio_sid = await get_sms_client().send(shop.phone, body)
            outcome = ESCALATION_SENT if twilio_sid else ESCALATION_FAILED
        except Exception:  # noqa: BLE001 - a gateway failure must not block the driver
            logger.exception("cod_dispute_escalation_failed", stop_id=str(stop_id))
            outcome = ESCALATION_FAILED

    session.add(
        Message(
            hub_id=hub_id,
            driver_id=driver_id,
            stop_id=stop_id,
            channel="shop",
            direction="outbound",
            body=body,
            counterparty_phone=shop.phone if shop else None,
            twilio_sid=twilio_sid,
        )
    )
    return outcome
