# Order status webhooks

**Who this is for:** a developer at an LMX client, wiring our status updates into
their own system. Configure endpoints in the client portal under **Integrations**.

Engineering context lives in `app/webhooks/` and `docs/ROADMAP.md` F4; this
document is the contract we owe a consumer.

---

## What you receive

One POST per order status change, to every active endpoint on your account.

```http
POST /your-endpoint HTTP/1.1
Content-Type: application/json
X-LMX-Signature: t=1754841600,v1=3ba7c9…
X-LMX-Event-Id: 6f1c2a5e-...
X-LMX-Delivery-Attempt: 1
User-Agent: LMX-OS-Webhooks/1

{
  "event_id": "6f1c2a5e-...",
  "type": "order.status_changed",
  "order_id": "9d2b...",
  "source_order_ref": "EPICOR-99812",
  "source_system": "epicor",
  "previous_status": "PICKED_UP",
  "status": "DELIVERED",
  "occurred_at": "2026-08-10T14:03:11.204000+00:00"
}
```

`source_order_ref` is your own system's identifier for the order. It's here
because a webhook that identifies an order only by our UUID makes you do a lookup
you may not be able to do.

### Statuses

`RECEIVED`, `CLASSIFIED`, `HELD`, `QUEUED`, `ASSIGNED`, `ACCEPTED`,
`EN_ROUTE_PICKUP`, `PICKED_UP`, `EN_ROUTE_DROP`, `DELIVERED`, `EXCEPTION_RAISED`,
`RETURNED_TO_HUB`, `CANCELLED`.

Treat unknown values as informational and don't fail on them — we add states as
the operation grows, and a handler that 400s on an unrecognised status will stop
receiving the ones it does understand (see *Responding*, below).

---

## Verifying the signature

**Do this before trusting anything in the body.** The endpoint URL is not a
secret: anyone who learns it can POST to it. The signature is what makes a request
ours.

`X-LMX-Signature` is `t=<unix seconds>,v1=<hex hmac>`, where the HMAC is
SHA-256 over `"{t}.{raw request body}"`, keyed by your endpoint's signing secret.

```python
import hmac, time
from hashlib import sha256

def verify(secret: str, raw_body: bytes, header: str, tolerance: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    if "t" not in parts or "v1" not in parts:
        return False
    timestamp = int(parts["t"])
    if abs(time.time() - timestamp) > tolerance:
        return False                       # too old — probably a replay
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, sha256
    ).hexdigest()
    return hmac.compare_digest(parts["v1"], expected)
```

Three things that are easy to get wrong:

1. **Sign the raw body, not a re-serialised object.** Parsing to JSON and dumping
   it again reorders keys and changes whitespace, and the signature won't match.
2. **Check the timestamp.** It is inside the signed string, so we cannot fake it —
   but if you ignore it, a captured request stays replayable forever, and a
   replayed `DELIVERED` is you marking an order complete that isn't.
3. **Use a constant-time comparison.** `==` on a signature leaks it a byte at a
   time to anyone who can retry.

The secret is shown once, when you create the endpoint, and is never returned
again by any API call. Lost it? Delete the endpoint and add a new one.

---

## Responding

| You return | We do |
|---|---|
| any `2xx` | Done. |
| `408`, `425`, `429`, any `5xx` | Retry with backoff. |
| any other `4xx` | **Stop. No retry.** We take this as "you don't want this event." |

That last row is the one to be careful with. If your handler 400s on an event type
it doesn't recognise, you permanently lose those events. Return `200` for anything
you don't handle yet.

**Acknowledge fast and process later.** We wait 10 seconds. Queue the event on your
side and return immediately rather than doing work inline — a slow handler becomes
a failed delivery, and enough failed deliveries pause your endpoint.

---

## Retries, ordering and duplicates

**Retry schedule:** roughly 1 min, 5 min, 25 min, 2 h, 10 h, 2 days — six attempts
over about three days. Enough to ride out a weekend deploy of your system.

**Delivery is at-least-once.** A response we never see (your `200` lost to a
timeout) means we try again. **Dedupe on `event_id`**, which is stable across every
attempt of the same event. `X-LMX-Delivery-Attempt` tells you which try this is.

**Arrival order is not event order.** A `PICKED_UP` that failed twice can land
after the `DELIVERED` that followed it. Order by `occurred_at`, and don't move an
order backwards: if you've already recorded `DELIVERED`, ignore an arriving
`PICKED_UP` rather than overwriting.

**Your endpoint can be paused.** After 20 consecutive failed deliveries we
deactivate it — an endpoint dead that long isn't coming back on its own. It shows
as *Paused* in the portal with the reason, and you resume it there once your side
is fixed. Rejections (the `4xx` row above) don't count toward this, so an
unimplemented event type won't cost you the ones you handle.

**Events during a pause are not backfilled.** Nothing is enqueued for an inactive
endpoint, so reconcile through the portal or the API for the window it was off.

---

## Requirements on the URL

- **`https://` only.** The signature proves the body is ours, but over plaintext
  anyone on the path reads your customer's address and order history.
- **Publicly resolvable.** We reject loopback, private ranges and link-local
  addresses, and we don't follow redirects — a `302` to an internal host is the
  same request we refused to make directly.
- **No credentials in the URL.** Authenticate with the signature; a
  `https://user:pass@…` URL would end up in our logs.

---

## Debugging

The portal's **Integrations** tab shows recent deliveries per endpoint with the
status code we got and the error we recorded — enough to tell a handler that 500s
from a URL that never resolved. Start there before asking us whether we sent
something.
