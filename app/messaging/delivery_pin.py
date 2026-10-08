"""
Delivery PINs for proof of delivery (docs/ROADMAP.md A4).

A PIN is generated the moment a dropoff stop is created (accept_offer,
app/api/driver_routes.py) and checked server-side against what the driver
submits at complete_stop time (Stop.pod_pin vs. Stop.delivery_pin), not just
recorded.

**The recipient reads it on the tracking page.** It used to be texted to them,
which needed Twilio and a phone number on the order. The tracking link now goes
to the client, in the portal and the order API, and the client forwards it to
their customer; the page shows the PIN until the delivery is made
(app/tracking/service.py). The driver never sees it. A recipient who never got
the link has no PIN to give, and the driver proves the delivery with a photo or
a signature instead.
"""
from __future__ import annotations

import secrets

PIN_LENGTH = 4
MAX_PIN_VERIFICATION_ATTEMPTS = 5


def generate_delivery_pin() -> str:
    return f"{secrets.randbelow(10 ** PIN_LENGTH):0{PIN_LENGTH}d}"
