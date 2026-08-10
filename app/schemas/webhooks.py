"""
Webhook configuration as a client sees it (docs/ROADMAP.md F4).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class WebhookEndpointBody(BaseModel):
    url: str
    description: str | None = None


class WebhookEndpointView(BaseModel):
    """An endpoint on the client's integrations screen.

    **No `secret`.** It is returned exactly once, by the create call, and never
    again - the same shape as any API key. Listing it would mean every page load of
    the integrations tab put a live signing key in a response, a browser cache and
    an access log.
    """

    endpoint_id: str
    url: str
    description: str | None
    is_active: bool
    # Surfaced so a client can see WHY their integration stopped, rather than
    # concluding we quietly stopped sending. An endpoint switched off after
    # sustained failure is the most likely reason a working integration goes quiet.
    consecutive_failures: int
    disabled_at: datetime | None
    last_success_at: datetime | None
    created_at: datetime


class WebhookEndpointCreated(WebhookEndpointView):
    """The create response, and the only place the secret appears.

    Named distinctly rather than making `secret` optional on the view above, so
    that returning it is a deliberate choice at one call site instead of a field
    that might get populated by accident.
    """

    secret: str


class WebhookDeliveryView(BaseModel):
    """One attempt history entry, for a client debugging their own handler.

    Exists because "did you send it?" is the first question of every webhook
    integration, and without this the honest answer is "check our logs", which they
    cannot do. Carries the status code and our error string so they can tell a
    handler that 500s from a URL that never resolved.
    """

    delivery_id: str
    order_id: str
    event_id: str
    status: str
    attempts: int
    last_status_code: int | None
    last_error: str | None
    next_attempt_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime


# ---------------------------------------------------------------------------
# API keys for inbound order submission (docs/ORDER_API.md)
# ---------------------------------------------------------------------------


class ApiKeyBody(BaseModel):
    description: str | None = None


class ApiKeyView(BaseModel):
    """A key on the client's integrations screen.

    **No token, and no way to recover one.** Only the hash is stored, so we could not
    return it even if we wanted to - which is the point: a database disclosure leaks
    nothing usable. `token_prefix` is what makes the list navigable, and it is what
    makes rotation safe: revoking the wrong key is otherwise a coin flip.
    """

    api_key_id: str
    token_prefix: str
    description: str | None
    is_active: bool
    # The field rotation depends on. A client with two keys needs to know which one
    # their system is actually using before revoking the other.
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyView):
    """The create response, and the only place the full token ever appears."""

    token: str
