"""
Working out who is actually calling (docs/ROADMAP.md L15).

One helper, used by every rate limiter, because three call sites each deciding
this for themselves is three chances to get it wrong in different ways.

**The problem.** Every limiter here keyed on `request.client.host` - the direct
TCP peer. That is correct when nothing sits in front of the app and completely
wrong the moment something does: behind a load balancer, every request appears to
come from the balancer, so a single shared bucket throttles the entire internet
as one caller. LMX Link made this urgent by adding three unauthenticated public
endpoints; before that the exposed surface was small enough to live with.

**Why the naive fix is worse than the bug.** `X-Forwarded-For` is a request
header, so a caller controls it. Taking the leftmost entry - which is what most
"just read XFF" advice amounts to - lets an attacker send
`X-Forwarded-For: 1.2.3.4` and mint a fresh, empty rate-limit bucket on every
single request. That is not a weaker limit, it is no limit at all, dressed up as
one.

**How this does it.** Each proxy in a chain APPENDS the address it received the
connection from. So with N trusted proxies in front, the last N entries were
written by our own infrastructure and everything to their left was supplied by
the caller and is worthless. The real client is the entry N from the right:

    TRUSTED_PROXY_COUNT=1, one ALB in front
      client sends:  X-Forwarded-For: 9.9.9.9        (a lie)
      ALB appends:   X-Forwarded-For: 9.9.9.9, 203.0.113.7
      we take:       203.0.113.7                    (index -1)

    TRUSTED_PROXY_COUNT=2, CDN then ALB
      CDN appends the real client, ALB appends the CDN
      X-Forwarded-For: <lies>, 203.0.113.7, 198.51.100.4
      we take:       203.0.113.7                    (index -2)

**It defaults to 0, which means "trust nothing, use the TCP peer".** That is
correct for local development and for the app running with no proxy, and it means
this change alters no behaviour until someone deliberately says how many proxies
exist. Setting it higher than the real number is the dangerous direction - it
starts trusting caller-supplied entries - so the default errs the safe way and the
setting's docstring says so.

**THIS MUST BE THE ONLY LAYER INTERPRETING FORWARDED HEADERS.** Uvicorn enables
proxy-header handling by default and will itself rewrite `request.client.host`
from `X-Forwarded-For` for connections coming from `--forwarded-allow-ips`. With
both layers active, "fall back to the peer" below can fall back to a value the
caller supplied - which quietly inverts the safety of the default. The Dockerfile
therefore runs uvicorn with `--no-proxy-headers`, and that flag is load-bearing
rather than tidiness: verified by request, a forged header resolves to the real
peer with it and to the forged value without it. If this app is ever started some
other way, that flag has to come too.
"""
from __future__ import annotations

import ipaddress

from starlette.requests import Request

from app.config import settings

# What to key on when there is genuinely nothing to go on - a test client with no
# peer, or a malformed forwarded chain. A shared bucket for these is the right
# failure mode: it throttles rather than exempts.
UNKNOWN = "unknown"


def _peer(request: Request) -> str:
    return request.client.host if request.client else UNKNOWN


def _looks_like_an_ip(value: str) -> bool:
    """Reject anything that isn't an address.

    Not cosmetic. Without it a caller can put arbitrary text in the header and,
    once we're behind a proxy, influence a Redis key - so an attacker could pick
    a bucket, or make an unbounded number of them out of junk. Rejecting
    non-addresses keeps the key space to real IPs.
    """
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def client_ip(request: Request) -> str:
    """The caller's address, accounting for however many proxies are in front.

    Falls back to the TCP peer whenever the forwarded chain can't be trusted -
    unset, too short for the configured proxy count, or not an address. Falling
    back means a legitimate caller behind a misconfigured proxy shares the
    proxy's bucket, which throttles too aggressively rather than not at all. That
    is the right way round to be wrong.
    """
    trusted = settings.trusted_proxy_count
    if trusted <= 0:
        return _peer(request)

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        # Configured for a proxy but nothing forwarded a header. Either a
        # misconfiguration or someone reaching the app directly, bypassing the
        # proxy - in both cases the peer is the most honest thing available.
        return _peer(request)

    entries = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(entries) < trusted:
        # Shorter than our own infrastructure should have made it. Trusting any
        # entry here would mean trusting one the caller may have written.
        return _peer(request)

    candidate = entries[-trusted]
    return candidate if _looks_like_an_ip(candidate) else _peer(request)
