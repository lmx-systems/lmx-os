"""
Telling the distributor their customer wouldn't pay (docs/ROADMAP.md W2).

The second clause of the rule - *"one tap escalates to the distributor"* - and the whole
value of it is that somebody who can act actually hears. A dispute recorded in a table
nobody is watching is the driver having stood there for nothing.

**Escalates to the CLIENT, not to LMX ops.** The money is the distributor's invoice to
their own customer, so they are the only party who can decide anything: waive it, insist
on it, phone the customer, send it again tomorrow. Routing this to LMX ops first would
insert us into a commercial dispute we are not part of.

**By email to the client's portal admins.** It used to be a text to the shop's phone,
which needed Twilio. The client's admins are the people with an account here and an
address on file; a shop has neither. Email is slower than a text while the customer is
still standing there, which is the cost of having no SMS provider.

Deliberately says what was disputed and nothing about who is right. The driver's note is
passed along as the customer's account, because a pattern across an account is the useful
signal and paraphrasing it would lose that.
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.messaging.client_emails import send_best_effort
from app.messaging.email_client import get_email_client
from app.models.client_user import CLIENT_ADMIN_ROLE, ClientUser

logger = structlog.get_logger(__name__)

_SUBJECT = "A customer declined to pay on delivery"

_TEMPLATE = """Your customer at {address} declined to pay the ${amount:.2f} due on order
{reference}. Our driver did not negotiate and has moved on.
{note}
Please contact your customer and let us know how to proceed.

— LMX
"""


def _body(*, address: str, amount_cents: int, reference: str, note: str | None) -> str:
    return _TEMPLATE.format(
        address=address or "the delivery address",
        amount=(amount_cents or 0) / 100,
        reference=reference or "(no reference)",
        note=f'They said: "{note}".\n' if note else "",
    )


# What happened to the escalation, which is not a boolean.
#
# With no mail server configured nothing is sent, and reporting that as N per-dispute
# failures would cry wolf permanently. That is one deployment-wide setting, not a
# per-dispute failure, and the dispute report says which.
ESCALATION_SENT = "sent"
ESCALATION_NO_RECIPIENT = "no_client_admin"
ESCALATION_NOT_CONFIGURED = "email_not_configured"
ESCALATION_FAILED = "send_failed"


def email_is_configured() -> bool:
    """Whether a real mail server exists on this deployment.

    Read by the dispute report so a reader knows whether `unescalated_count` means "these
    distributors were not told" or "nothing is being sent at all yet".
    """
    return get_email_client().engine_name != "stub"


async def notify_client_of_cod_dispute(
    session: AsyncSession,
    *,
    stop_id: uuid.UUID,
    client_id: uuid.UUID | None,
    delivery_address: str | None,
    amount_cents: int,
    reference: str,
    note: str | None,
) -> str:
    """Email the client's active portal admins. Returns one of the ESCALATION_* outcomes.

    Only `ESCALATION_SENT` sets `CodCollection.escalated_at`: a dispute nobody was told
    about is a real state, and recording it as escalated anyway would hide the one failure
    that breaks the promise this feature makes.

    Best-effort, like every send in this codebase - a driver who has already left must not
    be blocked by a mail server, and the dispute row is committed either way.
    """
    if client_id is None:
        logger.warning(
            "cod_dispute_not_escalated",
            stop_id=str(stop_id),
            reason=ESCALATION_NO_RECIPIENT,
            detail="the order has no client to tell",
        )
        return ESCALATION_NO_RECIPIENT
    admins = (
        await session.scalars(
            select(ClientUser.email).where(
                ClientUser.client_id == client_id,
                ClientUser.role == CLIENT_ADMIN_ROLE,
                ClientUser.is_active.is_(True),
            )
        )
    ).all()
    if not admins:
        logger.warning(
            "cod_dispute_not_escalated",
            stop_id=str(stop_id),
            reason=ESCALATION_NO_RECIPIENT,
            detail="the client has no active portal admin to email",
        )
        return ESCALATION_NO_RECIPIENT
    if not email_is_configured():
        logger.warning(
            "cod_dispute_not_escalated",
            stop_id=str(stop_id),
            reason=ESCALATION_NOT_CONFIGURED,
            detail="no mail server on this deployment - nothing was actually sent",
        )
        return ESCALATION_NOT_CONFIGURED

    body = _body(
        address=delivery_address or "",
        amount_cents=amount_cents,
        reference=reference,
        note=note,
    )
    sent = [await send_best_effort(to=address, subject=_SUBJECT, body=body) for address in admins]
    return ESCALATION_SENT if any(sent) else ESCALATION_FAILED
