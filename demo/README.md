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
| `ids.py` | Shared, fixed IDs so both scripts agree on which Hub/Client/Shop/Driver they mean. |

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

## Known limitation, worth knowing before you present this

`Order.status` in Postgres currently only ever reaches `held` — nothing
yet writes `assigned` back to the database once the optimizer dispatches
an order (route/stop persistence for component 7 isn't built out yet).
The **Hold Queue** and **Fleet Overview** dashboard widgets are accurate
live signals (that's what this demo relies on); the **Order Status
Summary** widget will keep showing the order under "held" even after it's
actually been dispatched. Best to avoid that specific widget in a live
walkthrough, or flag it as "status write-back is next," rather than let it
look like a bug in the room. Happy to close that gap before a real investor
session if you want it fixed — it's a contained addition (an `assigned_at`
write plus a status update in `DispatchOptimizerService.run_cycle`), just
out of scope for "build a sample payload."
