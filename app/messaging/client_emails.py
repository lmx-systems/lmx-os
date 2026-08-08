"""
The emails LMX Link sends a client (docs/LMX_LINK_PLAN.md).

Two today, both tied to the signup funnel:

  1. **Application received** - the receipt for "we'll be in touch". The page
     already says it; this is the version they still have tomorrow.
  2. **Approved** - the one that actually matters. Approval activates their login,
     and without this nobody tells them, so a self-serve signup ends in a phone
     call and the whole premise falls over.

Both are best-effort and neither can fail the request that triggered it. Blocking
an approval because mail is down would be strictly worse than a client who has to
be phoned - so a failed send is logged loudly and the approval still stands. The
ops signup panel shows the client as active either way, which is what lets
someone notice and pick up the phone.

Copy is written for a parts-counter reader, not a SaaS user: short lines, no
onboarding funnel language, no "welcome aboard". The tone LMX Link's naming
decision asks for - this is a carrier telling a business it can start sending
work, not software announcing itself.
"""
from __future__ import annotations

import structlog

from app.config import settings
from app.messaging.email_client import get_email_client

logger = structlog.get_logger(__name__)


async def _send(*, to: str, subject: str, body: str) -> bool:
    """Send, and guarantee no exception reaches the caller.

    `EmailClient.send` is documented as never raising and the SMTP client honours
    it - but a client implementation is ordinary code, and an approval endpoint is
    the wrong place to discover that a provider misbehaved. Same reasoning as the
    per-sink guard in `app/orders/sinks.py`.

    Found by test: an email client that raised took the whole approval down with
    it, which is precisely the outcome "best-effort" is supposed to rule out.
    """
    try:
        return await get_email_client().send(to=to, subject=subject, body=body)
    except Exception:  # noqa: BLE001 - a broken mailer must never fail a request
        logger.warning("email_client_raised", subject=subject)
        return False


async def send_signup_received_email(*, to: str, contact_name: str, company_name: str) -> bool:
    """Acknowledge an application.

    Only ever sent for a genuinely new signup. A duplicate submission
    deliberately does NOT trigger this: mailing the real owner of an address
    "we got your signup" when they didn't submit one would both alarm them and
    confirm to whoever did submit it that the address is already registered -
    the exact disclosure the pre-charged rate limiter exists to prevent.
    """
    body = f"""Hi {contact_name},

Thanks for your interest in LMX. We've got the details for {company_name} and
our team is reviewing them now.

Once you're approved we'll email you a link to sign in, and you'll be able to
start sending us deliveries straight away.

If anything changes in the meantime, just reply to this message.

— LMX
"""
    sent = await _send(to=to, subject="We've got your details", body=body)
    if not sent:
        logger.warning("signup_received_email_not_sent", company=company_name)
    return sent


async def send_signup_approved_email(*, to: str, contact_name: str, company_name: str) -> bool:
    """Tell a client they can start sending work.

    The single most important email here. Approval flips their login active, and
    an activated account nobody knows about is the same as no account.
    """
    sign_in_url = settings.portal_base_url.rstrip("/")
    body = f"""Hi {contact_name},

{company_name} is approved — you can start sending us deliveries now.

Sign in here: {sign_in_url}

Use the email address you signed up with, and the password you chose. From
there, "New order" is all you need: type where it's going, where to collect
from, and how soon. We'll confirm a collection time as soon as you send it.

Any questions, just reply.

— LMX
"""
    sent = await _send(
        to=to, subject="You're approved — you can start sending deliveries", body=body
    )
    if not sent:
        # Loud, because the consequence is a client sitting on an active account
        # they don't know exists. Whoever approved them can still phone.
        logger.warning("signup_approved_email_not_sent", company=company_name, to_hint=to[:3])
    return sent


async def send_password_reset_email(
    *, to: str, contact_name: str, reset_url: str
) -> bool:
    """The reset link (docs/ROADMAP.md L14).

    Only ever sent to an address that belongs to an ACTIVE client user. A pending
    applicant deliberately gets nothing: mailing them a reset link would confirm
    that their application exists, and resetting the password wouldn't grant
    access anyway since C4 re-checks `is_active` on every request.

    The copy states the expiry and that an unrequested email can be ignored.
    Both matter more than they look - the first stops a support call an hour
    later, and the second is what someone needs to read if this arrives because
    an attacker typed their address.
    """
    body = f"""Hi {contact_name},

Someone asked to reset the password for your LMX account. If that was you, use
this link within the next hour:

{reset_url}

If it wasn't you, you can ignore this - your password hasn't changed, and
whoever asked can't see this message.

— LMX
"""
    sent = await _send(to=to, subject="Reset your LMX password", body=body)
    if not sent:
        logger.warning("password_reset_email_not_sent", to_hint=to[:3])
    return sent
