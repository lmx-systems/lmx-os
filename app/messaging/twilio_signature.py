"""
Twilio webhook request-signature validation (roadmap item S7).

Twilio signs every webhook it sends: X-Twilio-Signature is
base64(HMAC-SHA1(auth_token, url + concat(sorted form params as key+value))).
Verifying it is what stops anyone who discovers the webhook URL from
injecting fake "inbound SMS" into the messaging tables.

Pure function (no I/O, no framework types) so it's trivially unit-testable
against Twilio's documented algorithm - the FastAPI wiring lives in
app/api/webhooks.py.
"""
from __future__ import annotations

import base64
import hashlib
import hmac


def compute_signature(auth_token: str, url: str, form_params: dict[str, str]) -> str:
    """The signature Twilio would produce for this request."""
    payload = url + "".join(f"{key}{form_params[key]}" for key in sorted(form_params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def signature_is_valid(
    auth_token: str, url: str, form_params: dict[str, str], provided_signature: str | None
) -> bool:
    """Constant-time comparison - never compare signatures with ==."""
    if not provided_signature:
        return False
    expected = compute_signature(auth_token, url, form_params)
    return hmac.compare_digest(expected, provided_signature)
