"""
Working out the real caller behind a proxy (docs/ROADMAP.md L15).

Every rate limiter in this app keys on the value `client_ip` returns, so getting
it wrong has one of two consequences and they are not symmetric:

  - **Too trusting** - taking an entry the caller supplied - lets an attacker mint
    a fresh empty bucket on every request. That is not a weaker limit, it is no
    limit at all while looking like one. The spoofing tests below are the reason
    this module exists rather than a one-line header read.
  - **Too suspicious** - falling back to the proxy's own address - throttles a
    shared bucket too aggressively. Annoying, and safe.

The tests are written to hold that asymmetry in place: every ambiguous or
malformed case must land on the second outcome.
"""
import pytest

from app.client_ip import UNKNOWN, client_ip
from app.config import settings

PROXY = "10.0.0.5"
REAL_CLIENT = "203.0.113.7"
CDN = "198.51.100.4"
ATTACKER_CLAIM = "1.2.3.4"


class _Request:
    """Just the two things client_ip reads."""

    def __init__(self, *, peer: str | None, forwarded: str | None = None) -> None:
        self.client = type("C", (), {"host": peer})() if peer is not None else None
        self.headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}


@pytest.fixture
def proxies(monkeypatch):
    def _set(count: int) -> None:
        monkeypatch.setattr(settings, "trusted_proxy_count", count)

    return _set


# ---------------------------------------------------------------------------
# No proxy: the default, and it must change nothing
# ---------------------------------------------------------------------------


def test_with_no_proxies_configured_the_tcp_peer_is_used(proxies):
    """The default is 0, so this change alters no behaviour until someone
    deliberately declares how many proxies exist."""
    proxies(0)
    assert client_ip(_Request(peer=REAL_CLIENT)) == REAL_CLIENT


def test_with_no_proxies_a_forwarded_header_is_ignored_entirely(proxies):
    """The most important test in the file. With nothing in front of us, an
    X-Forwarded-For header can only have come from the caller - so honouring it
    would hand every caller their own private rate-limit bucket."""
    proxies(0)
    request = _Request(peer=REAL_CLIENT, forwarded=ATTACKER_CLAIM)
    assert client_ip(request) == REAL_CLIENT


# ---------------------------------------------------------------------------
# One proxy: the deployed shape
# ---------------------------------------------------------------------------


def test_one_proxy_takes_the_entry_the_proxy_appended(proxies):
    """The balancer appends the peer it actually saw, so the last entry is the
    only trustworthy one."""
    proxies(1)
    request = _Request(peer=PROXY, forwarded=f"{ATTACKER_CLAIM}, {REAL_CLIENT}")
    assert client_ip(request) == REAL_CLIENT


def test_one_proxy_ignores_everything_the_caller_prepended(proxies):
    """Several fabricated entries change nothing - only the rightmost was written
    by our own infrastructure."""
    proxies(1)
    forwarded = f"{ATTACKER_CLAIM}, 5.5.5.5, 6.6.6.6, {REAL_CLIENT}"
    assert client_ip(_Request(peer=PROXY, forwarded=forwarded)) == REAL_CLIENT


def test_a_single_entry_chain_is_the_real_client(proxies):
    """The normal case: a caller who sent no header at all, so the balancer's
    appended entry is the only one."""
    proxies(1)
    assert client_ip(_Request(peer=PROXY, forwarded=REAL_CLIENT)) == REAL_CLIENT


# ---------------------------------------------------------------------------
# Two proxies: a CDN in front of a balancer
# ---------------------------------------------------------------------------


def test_two_proxies_take_the_second_from_the_right(proxies):
    """CDN appends the real client, balancer appends the CDN."""
    proxies(2)
    forwarded = f"{ATTACKER_CLAIM}, {REAL_CLIENT}, {CDN}"
    assert client_ip(_Request(peer=PROXY, forwarded=forwarded)) == REAL_CLIENT


def test_claiming_more_proxies_than_exist_falls_back_rather_than_trusting(proxies):
    """The dangerous misconfiguration, made safe. With two declared but only one
    real entry, there is no entry we can vouch for - so use the peer instead of
    reaching further left into caller-written territory."""
    proxies(2)
    assert client_ip(_Request(peer=PROXY, forwarded=REAL_CLIENT)) == PROXY


# ---------------------------------------------------------------------------
# Everything ambiguous falls back to the peer
# ---------------------------------------------------------------------------


def test_a_missing_header_behind_a_proxy_falls_back(proxies):
    """Either a misconfiguration or someone reaching the app directly and
    bypassing the balancer. The peer is the most honest thing available."""
    proxies(1)
    assert client_ip(_Request(peer=PROXY)) == PROXY


def test_an_empty_header_falls_back(proxies):
    proxies(1)
    assert client_ip(_Request(peer=PROXY, forwarded="   ")) == PROXY


@pytest.mark.parametrize(
    "junk",
    [
        "not-an-ip",
        "'; DROP TABLE orders; --",
        "a" * 500,
        "example.com",
    ],
)
def test_a_non_address_falls_back_instead_of_becoming_a_redis_key(proxies, junk):
    """Without this, a caller behind a proxy could put arbitrary text into a
    rate-limit key - choosing a bucket, or manufacturing unlimited junk ones."""
    proxies(1)
    assert client_ip(_Request(peer=PROXY, forwarded=f"{ATTACKER_CLAIM}, {junk}")) == PROXY


def test_ipv6_is_accepted(proxies):
    """A real address in a real deployment - rejecting it would silently throw
    every IPv6 caller into the shared proxy bucket."""
    proxies(1)
    v6 = "2001:db8::1"
    assert client_ip(_Request(peer=PROXY, forwarded=v6)) == v6


def test_no_peer_and_no_header_is_a_shared_bucket_not_an_exemption(proxies):
    """Nothing to go on. Sharing one bucket throttles; returning something unique
    per request would exempt."""
    proxies(0)
    assert client_ip(_Request(peer=None)) == UNKNOWN


def test_whitespace_around_entries_is_tolerated(proxies):
    """Real proxies emit ", " and some emit ",". Neither should change the answer."""
    proxies(1)
    assert client_ip(_Request(peer=PROXY, forwarded=f"{ATTACKER_CLAIM},{REAL_CLIENT}")) == REAL_CLIENT
