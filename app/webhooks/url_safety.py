"""
Whether a client-supplied URL is safe for this server to POST to
(docs/ROADMAP.md F4).

**A webhook endpoint is an SSRF primitive, not a config field.** The client types a
URL and our backend then makes an authenticated-from-inside-the-network request to
it, on a schedule, with retries. Without this check a client - or anyone who
compromises a client's portal login - can aim that at:

  - `http://169.254.169.254/latest/meta-data/` and friends, the cloud metadata
    service, which on a misconfigured instance hands out credentials;
  - `http://localhost:8000/internal/dispatch/run-all`, our own internal router;
  - any private-range host reachable from the VPC that isn't reachable from the
    internet - a database admin panel, another service's health endpoint.

The response body never comes back to them, which limits this to a blind SSRF, but
blind is still enough to trigger side effects and to map an internal network by
timing.

**What this deliberately does NOT solve: DNS rebinding.** We resolve the hostname
here, at save time, and the name can resolve differently when the delivery
actually fires. Closing that properly means resolving at request time and pinning
the connection to the vetted address, which httpx does not do for us. It is
recorded rather than quietly ignored, and the mitigations that make it much less
attractive are in place: https only, no redirect following, and a response body we
never return to the caller.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)


class UnsafeWebhookUrl(Exception):
    """Rejected before it is stored. The message is shown to the client, so it says
    what to change rather than what we were afraid of."""


# https only. Not pedantry: the signature proves the body came from us, but over
# plaintext anyone on the path still reads a customer's address and order history
# out of the request.
_ALLOWED_SCHEMES = ("https",)

# Hostnames that are never a legitimate customer integration, checked before DNS
# so an obvious attempt doesn't even cause a lookup.
_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "instance-data"}
)


def _resolve(hostname: str, port: int) -> set[str]:
    """Every address this hostname currently resolves to.

    A named seam so the safety logic is testable without depending on live DNS -
    which would otherwise make these tests network-dependent and, worse, make the
    private-address check untestable except via literal IPs.
    """
    resolved = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    return {info[4][0] for info in resolved}


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    # `is_global` is the right predicate rather than a hand-rolled list of ranges:
    # it already excludes loopback, link-local (which is where 169.254.169.254
    # lives), private, reserved, multicast and unspecified, for both v4 and v6.
    return ip.is_global


def validate_webhook_url(url: str) -> str:
    """Return the URL if it is safe to POST to, else raise `UnsafeWebhookUrl`."""
    parsed = urlparse(url.strip())

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeWebhookUrl("The URL must start with https://")
    if not parsed.hostname:
        raise UnsafeWebhookUrl("That doesn't look like a complete URL")
    if parsed.username or parsed.password:
        # Credentials in the URL would end up in our logs, and they are not how
        # this authenticates - the signature is.
        raise UnsafeWebhookUrl("Remove the username and password from the URL")

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in _BLOCKED_HOSTNAMES:
        raise UnsafeWebhookUrl("The URL must point at a public address we can reach")

    # A literal IP skips DNS entirely and is checked directly.
    try:
        if not _is_public(hostname):
            raise UnsafeWebhookUrl("The URL must point at a public address we can reach")
        return url.strip()
    except ValueError:
        pass  # not a literal address - resolve it below

    try:
        addresses = _resolve(hostname, parsed.port or 443)
    except socket.gaierror as exc:
        # Fails CLOSED. We cannot tell a public host from a private one without a
        # lookup, and guessing in the permissive direction is how `internal-api`
        # gets saved during a DNS blip. The cost is a client retrying; the cost the
        # other way is an SSRF target stored permanently.
        raise UnsafeWebhookUrl(
            "We couldn't look up that hostname - check it and try again"
        ) from exc

    if not addresses:
        raise UnsafeWebhookUrl("We couldn't look up that hostname - check it and try again")

    # EVERY address must be public, not merely the first. A host that resolves to
    # both a public and a private address would otherwise pass on one lookup and
    # reach the private one on the next.
    for address in addresses:
        if not _is_public(address):
            logger.warning(
                "webhook_url_rejected_private_address",
                hostname=hostname,
                address=address,
            )
            raise UnsafeWebhookUrl("The URL must point at a public address we can reach")

    return url.strip()
