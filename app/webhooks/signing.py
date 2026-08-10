"""
Proving an outbound webhook came from us (docs/ROADMAP.md F4).

HMAC-SHA256 over `{timestamp}.{body}`, keyed by the endpoint's secret, hex
encoded. Sent as:

    X-LMX-Signature: t=1754841600,v1=3ba7...
    X-LMX-Event-Id: <uuid>
    X-LMX-Delivery-Attempt: 1

**The timestamp is inside the signed string, not merely alongside it.** Signing the
body alone produces a token that stays valid forever, so anyone who captures one
request can replay it indefinitely - and a replayed "delivered" webhook is a
consumer marking an order complete that isn't. Binding the timestamp into the
digest means a consumer can reject anything older than their own tolerance and the
attacker cannot move the clock forward without breaking the signature.

`v1=` prefixes the digest so the scheme can change without breaking every consumer
on the same day - they match on the version they know and ignore the rest.

The verifier is here too, and it is not dead code: it is what the documentation
tells clients to implement, so it needs to exist somewhere tested rather than as a
snippet in prose that nobody ever ran.
"""
from __future__ import annotations

import hmac
from hashlib import sha256

SIGNATURE_VERSION = "v1"


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """The value for the X-LMX-Signature header."""
    digest = _digest(secret, body, timestamp)
    return f"t={timestamp},{SIGNATURE_VERSION}={digest}"


def _digest(secret: str, body: bytes, timestamp: int) -> str:
    signed_payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), signed_payload, sha256).hexdigest()


def verify(
    secret: str, body: bytes, header: str, *, now: int, tolerance_seconds: int = 300
) -> bool:
    """What a consumer implements. Rejects a stale timestamp as well as a bad digest.

    Compared with `hmac.compare_digest` rather than `==`: a webhook signature is
    exactly the kind of secret a timing side channel leaks, one byte at a time,
    given a caller who can retry as often as they like.
    """
    parts = dict(
        piece.split("=", 1) for piece in header.split(",") if "=" in piece
    )
    provided = parts.get(SIGNATURE_VERSION)
    raw_timestamp = parts.get("t")
    if not provided or not raw_timestamp:
        return False
    try:
        timestamp = int(raw_timestamp)
    except ValueError:
        return False
    if abs(now - timestamp) > tolerance_seconds:
        return False
    return hmac.compare_digest(provided, _digest(secret, body, timestamp))
