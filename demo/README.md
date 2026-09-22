# Investor demo: one sample order, end to end

This is a self-contained walkthrough of the Phase 1 pipeline using one
realistic sample order — no real client data needed. It's meant to be run
live: an "urgent brake parts" order comes in from an auto-parts shop, and
you watch the system classify it, hold it briefly, and dispatch it to a
driver automatically.

## What's in here

| File | Purpose |
|---|---|
| `epicor_sample_order.json` | One sample Epicor webhook payload — a rush brake-parts order. Field shape matches `app/ingestion/adapters/epicor.py`. |
| `seed_demo_data.py` | Creates the one Hub / Client / Shop / Driver this payload needs to actually ingest and dispatch. Safe to re-run. |
| `send_demo_order.py` | Sends the order, watches it get classified and automatically dispatched, prints each step. |
| `ids.py` | Shared, fixed IDs (and the driver's phone) so the scripts agree on which Hub/Client/Shop/Driver they mean. |
| `run_full_loop.py` | **The whole pipeline in one command** - a CSV manifest lands, the hold queue holds it, the optimizer offers it, and a driver delivers it with a real photo. `--pace` slows it for an audience. |
| `INVESTOR_RUNBOOK.md` | **Presenting it live** - the four screens, the handset setup, what the two-minute hold is for, and what to disclaim before being asked. |

## Running it

1. Start the stack: `docker compose up -d`
2. Seed the demo data (once per fresh stack — re-running is harmless):
   ```
   docker compose exec app python -m demo.seed_demo_data
   ```
3. Send the sample order and watch it flow through:
   ```
   docker compose exec app python -m demo.send_demo_order
   ```
4. Open the dashboard at `http://localhost:5173` — Fleet Overview and Hold
   Queue both reflect this live if you want a visual alongside the terminal
   output.

Re-running step 3 sends a new order each time (fresh `OrderNum`-adjacent
timestamp), so you can repeat the demo without resetting anything.

## What this actually demonstrates

- **Real ingestion contract, not a mock.** The payload shape (`OrderNum`,
  `ShipToNum`, `ShipToLat/Lng`, `OrderDate`, `PriorityCode`, `ShipVia`) is
  what `EpicorAdapter` expects — this is the same endpoint a real Epicor
  webhook would call. Swapping in a real client later doesn't change the
  demo mechanics, just where the payload comes from.
- **Automatic classification.** `PriorityCode: RUSH` drives the Dynamic
  SLA Engine to tag it T1 (urgent) rather than the T2 default.
- **Automatic dispatch, no button-clicking.** Ingestion publishes an
  event the Dispatch Optimizer already listens for — the order gets
  matched to the seeded driver on its own within a couple seconds, which
  is the point `send_demo_order.py` waits for and confirms.

## Order status now reflects dispatch too

`DispatchOptimizerService.run_cycle` writes `Order.status = assigned` (plus
a new `assigned_at` timestamp) back to Postgres the moment it actually
dispatches an order — see `app/optimizer/service.py`. All three dashboard
widgets (Hold Queue, Fleet Overview, Order Status Summary) are safe to show
live now; none of them go stale after a dispatch.

Still not persisted: which route/stop an order landed on, or route
sequencing/ETAs for a driver's shift — that's `routes`/`stops` persistence,
tied to the driver app (component 7), and a separate, larger piece of work
than this status write-back.


## The full loop: CSV in, delivered out

`send_demo_order.py` stops at "dispatched", which is where the interesting half
begins. `run_full_loop.py` carries on to the end:

```
python -m demo.seed_demo_data
python -m scripts.create_ops_user --email demo@lmxit.com --password "demo-password" --name "Demo" --role admin
python -m scripts.create_client_user --client-id <CLIENT_ID from the seeder> \
    --email demo-client@example.com --password "demo-password-123" --name "Demo Client" --role admin
python -m demo.run_full_loop
```

It drops a CSV manifest the way a distributor would, waits out the hold,
accepts the offer as the driver, records a geofence crossing at every stop, and
delivers. Roughly two minutes, most of it the HOT_SHOT hold window.

**Every call is a real HTTP endpoint** - no internal function calls, no
database writes of its own. If it passes, the same calls work from the driver
app, because they are the same calls.

**It needs no external service.** Without `GOOGLE_CLOUD_PROJECT_ID` the
optimizer uses its nearest-neighbour stub; without Twilio the OTP comes back in
the response. Both are deliberate unconfigured-to-stub paths.

### What it does not show

- **A live routing solve.** The stub does not model time, so sequencing and
  ETAs are not what Google would return (`DEC-3`/`E1`).
- **Any SMS.** Shop and recipient notifications go to the Twilio stub.
- **Real geography.** The addresses are Austin; the design partner is not.
- **A real phone.** The script plays the driver over HTTP. To use the app
  itself, set the server address in the app's Profile - the built-in default is
  `localhost`, which on a handset means the handset. `INVESTOR_RUNBOOK.md`
  covers doing it this way, which is the version worth showing.

### Proof of delivery is now an actual photo

`run_full_loop` used to complete each stop with
`photo_url: "https://example.invalid/pod.jpg"` - a placeholder that made the run
pass and made *"delivered, with proof"* undemonstrable.

It now walks the real two-step path: request an upload URL, PUT the bytes,
submit what comes back. Set `PHOTO_STORAGE_DIR` and `MEDIA_BASE_URL` and the
photo is stored by this API and **rendered on the recipient's tracking page** -
which it never was before. `Stop.pod_photo_url` had been written since the app
got a camera and read by exactly one thing in the backend, an idempotency
comparison.

With no storage configured the stub still issues a `local-capture://` marker,
the run still passes, and the page renders nothing rather than a broken frame.
