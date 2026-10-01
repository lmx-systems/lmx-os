"""The SSRF guard's resolver fails closed.

`_resolve` turns a webhook hostname into the addresses `validate_webhook_url`
checks against `is_global`. A TCP lookup only ever yields IP strings, but the
socket types allow other shapes - and an address the guard cannot check has to
refuse the URL, not be dropped from the set of addresses it checks.
"""
import socket

import pytest

from app.webhooks import url_safety
from app.webhooks.url_safety import UnsafeWebhookUrl, _resolve


def _getaddrinfo_returning(*hosts):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (h, port)) for h in hosts]

    return fake


def test_every_resolved_address_is_returned(monkeypatch):
    monkeypatch.setattr(
        url_safety.socket, "getaddrinfo",
        _getaddrinfo_returning("93.184.216.34", "2606:2800:220:1::1"),
    )
    assert _resolve("example.com", 443) == {"93.184.216.34", "2606:2800:220:1::1"}


def test_an_address_the_guard_cannot_check_refuses_the_url(monkeypatch):
    monkeypatch.setattr(
        url_safety.socket, "getaddrinfo",
        _getaddrinfo_returning("93.184.216.34", 3232235777),
    )
    with pytest.raises(UnsafeWebhookUrl):
        _resolve("example.com", 443)
