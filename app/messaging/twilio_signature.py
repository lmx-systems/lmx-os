"""
Twilio inbound-webhook request-signature verification
(app/api/webhooks.py) - closes docs/NEXT_STEPS.md item 14's flagged gap
(a) and docs/ROADMAP.md's S7.

Twilio's own algorithm (https://www.twilio.com/docs/usage/security#validating-requests):
HMAC-SHA1 over the webhook's full public URL with every POST parameter
Twilio actually sent - name immediately followed by value, no delimiter,
sorted by parameter name - appended to it, keyed by the Twilio Auth
Token, base64-encoded. Every parameter Twilio sent must be included, not
just the ones a given endpoint destructures via `Form(...)`, or the
recomputed signature won't match Twilio's real one even for a genuine
request.

Not the official `twilio` SDK - this codebase's Twilio client
(app/messaging/sms_client.py) is a small hand-rolled httpx client too,
not the SDK, so this matches that existing style rather than adding a
second way of talking to Twilio.
"""
from __future__ import annotations

import base64
import hmac
from hashlib import sha1


def compute_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    data = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def signature_is_valid(auth_token: str, url: str, params: dict[str, str], signature: str | None) -> bool:
    if not signature:
        return False
    expected = compute_signature(auth_token, url, params)
    return hmac.compare_digest(expected, signature)
