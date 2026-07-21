"""
Builds a real starlette Request carrying form-encoded data, for calling
the Twilio inbound-SMS webhook's route function directly in tests -
needed since roadmap item S7 changed its signature from individual
Form(...) params to the whole Request (the signature check must cover
every form param Twilio sent, not just the ones the handler reads).
"""
from __future__ import annotations

from urllib.parse import urlencode

from starlette.requests import Request

WEBHOOK_PATH = "/webhooks/twilio/inbound-sms"


def make_twilio_form_request(
    form: dict[str, str],
    signature: str | None = None,
    host: str = "testserver",
) -> Request:
    body = urlencode(form).encode("utf-8")
    headers = [
        (b"content-type", b"application/x-www-form-urlencoded"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"host", host.encode("ascii")),
    ]
    if signature is not None:
        headers.append((b"x-twilio-signature", signature.encode("ascii")))

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": WEBHOOK_PATH,
        "raw_path": WEBHOOK_PATH.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "server": (host, 80),
        "client": ("127.0.0.1", 1234),
    }

    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)
