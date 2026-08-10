"""
The public order API's request and response shapes (docs/ORDER_API.md).

**Written in the caller's vocabulary, not ours.** `your_order_ref`, `deliver_by`,
`delivery_address` - an external integrator should not have to learn what
`source_order_ref`, `hold_deadline` or `drop_address_raw` mean to write against this.
The mapping into the LMX Order Object happens in the route.

Deliberately smaller than `LMXOrder`. That object is designed against all three
demand paths and carries fields only we set (`sla_owner`, `assignment_scope`,
modality and revenue basis). Exposing it wholesale would publish internal levers as
a public contract, and the one thing that cannot be walked back later is a field
somebody has started sending.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ApiOrderBody(BaseModel):
    # The caller's own identifier. Required, because it is the idempotency key: a
    # POST that times out has to be safely retryable, and without a reference from
    # the caller there is nothing to match a retry against.
    your_order_ref: str = Field(min_length=1, max_length=120)

    # Free text. Geocoded and remembered as a shop on first use, so the second order
    # to the same place costs no extra setup (LMX_LINK_PLAN §2.2 principle 3).
    pickup_address: str = Field(min_length=3, max_length=255)
    pickup_contact_name: str | None = Field(default=None, max_length=120)
    pickup_contact_phone: str | None = Field(default=None, max_length=32)

    delivery_address: str = Field(min_length=3, max_length=255)
    delivery_contact_name: str | None = Field(default=None, max_length=120)
    # Worth sending: it is what lets us text the recipient a live tracking link when
    # their parts are collected (docs/ROADMAP.md F3).
    delivery_contact_phone: str | None = Field(default=None, max_length=32)
    delivery_notes: str | None = Field(default=None, max_length=500)

    ready_at: datetime | None = None
    # When the caller needs it there. Advisory: LMX classifies the order into an SLA
    # tier and the returned `collect_by` is the commitment - a caller cannot set their
    # own SLA by writing a tighter time here, which is `sla_owner` doing its job
    # (LMX_LINK_PLAN §1.3).
    deliver_by: datetime | None = None


class ApiOrderResult(BaseModel):
    order_id: str
    your_order_ref: str
    status: str
    sla_tier: str
    collect_by: datetime | None
    promised_at: datetime | None
    # True when this reference was already on file and no new order was created. The
    # caller's retry succeeded; it just didn't need to do anything.
    duplicate: bool
