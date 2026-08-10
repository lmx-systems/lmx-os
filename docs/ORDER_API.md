# Order API

**Who this is for:** a developer at an LMX client, sending us orders from their own
system. Get an API key in the client portal under **Integrations**.

Status updates flow back over webhooks — see [WEBHOOKS.md](WEBHOOKS.md). The two
together are the whole integration: orders in, status out, no LMX involvement per
order.

---

## Authenticating

Every request carries your key in a header:

```
X-LMX-Api-Key: lmxk_live_…
```

The key identifies **which client you are**. There is no client or hub id anywhere
in a request — you cannot submit an order on another company's behalf, and you don't
have to know your own identifiers.

The key is shown once, when you create it. We store only a hash, so it genuinely
cannot be recovered: if you lose it, create another and revoke the old one.

**Rotation.** Keep two keys live while you switch. Create the new one, deploy it,
watch *Last used* move to it in the portal, then revoke the old one. Revocation is
immediate and there is no un-revoke.

**Limits.** 600 requests per minute per key. A burst import is fine; the limit is
per key, so nothing another client does can affect you.

A rejected key always returns the same `401 Invalid API key`, whether it's unknown,
revoked, or your account isn't active. If you're sure the key is right, check the
portal — your account may be pending or suspended.

---

## Submit an order

```http
POST /api/v1/orders
X-LMX-Api-Key: lmxk_live_…
Content-Type: application/json

{
  "your_order_ref": "INVOICE-1001",
  "pickup_address": "1200 E 6th St, Austin TX",
  "pickup_contact_name": "Marco",
  "pickup_contact_phone": "+15125550188",
  "delivery_address": "900 Congress Ave, Austin TX",
  "delivery_contact_name": "Jamie",
  "delivery_contact_phone": "+15125550101",
  "delivery_notes": "Loading dock at the rear, ring for access",
  "ready_at": "2026-08-11T14:00:00Z",
  "deliver_by": "2026-08-11T17:00:00Z"
}
```

```json
201 Created
{
  "order_id": "9d2b…",
  "your_order_ref": "INVOICE-1001",
  "status": "held",
  "sla_tier": "T2",
  "collect_by": "2026-08-11T14:25:00Z",
  "promised_at": "2026-08-11T15:05:00Z",
  "duplicate": false
}
```

| Field | Required | Notes |
|---|---|---|
| `your_order_ref` | yes | **Your** identifier. It's the idempotency key — see below. Unique within your account; it can collide with other companies' references without any problem. |
| `pickup_address` | yes | Free text. We geocode it and remember it, so repeat pickups from the same place need no setup. |
| `delivery_address` | yes | Same. |
| `delivery_contact_phone` | no | Worth sending: it's how we text your customer a live tracking link when their parts are collected. |
| `deliver_by` | no | **Advisory.** See *What we commit to*. |
| `ready_at`, contacts, `delivery_notes` | no | |

### Retries are safe

Send the same `your_order_ref` twice and you get the **same order back** with
`"duplicate": true`, and no second delivery is created.

This matters more than it looks. A POST that times out leaves you unable to tell
whether we received it. Without idempotency your only safe choices are to never
retry — silently losing orders — or to reconcile by hand. So: **retry freely, and
treat `duplicate: true` as success.**

### What we commit to

`deliver_by` tells us what you need; it does not set your service level. LMX
classifies the order into an SLA tier from your contract terms, and the response's
`collect_by` and `promised_at` are the commitment. Writing a tighter `deliver_by`
does not buy a faster tier.

### Errors

| Status | Meaning |
|---|---|
| `401` | Key is missing, unknown, revoked, or your account isn't active. |
| `422` | We couldn't resolve the pickup address, or a field is malformed. The message says which. |
| `429` | Over the per-key limit. Back off and retry. |

**A `422` on an address is a refusal, not a hint.** We don't guess coordinates — a
wrong one sends a real van to the wrong place. Fix the address and resubmit.

---

## Look an order up

```http
GET /api/v1/orders/INVOICE-1001
X-LMX-Api-Key: lmxk_live_…
```

Returns the same shape as the submit response. **Queried by your reference, not
ours**, so you don't have to store a mapping.

Use this to reconcile: if your webhook endpoint was paused, nothing is queued for it
while it's off, so poll the affected references once you're back.

---

## The shape of a good integration

1. Submit on your side's "dispatch" action. Store our `order_id` if you like, but
   you never need it.
2. Retry on any timeout or `5xx` with your same `your_order_ref`.
3. Take status from webhooks, not by polling. Verify the signature
   ([WEBHOOKS.md](WEBHOOKS.md)) and return `200` fast.
4. Reconcile with `GET /api/v1/orders/{your_ref}` after any gap in webhooks.

---

## Notes

- **`/api/v1` is versioned** because it's a contract you write code against. Breaking
  changes get a new version; we'll add optional fields to `v1` without notice and
  won't remove or repurpose existing ones.
- **Native POS payloads** (Epicor field names and the like) go through a connector
  LMX configures against your tenant, not through this endpoint. Talk to us — this
  API is the canonical shape we support without per-customer work.
