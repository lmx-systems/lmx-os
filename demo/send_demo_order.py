"""
Fires the sample Epicor order at a running LMX OS API and walks through the
whole pipeline live: ingest -> SLA classification -> hold queue -> the
Dispatch Optimizer automatically re-running off that event -> driver
assignment. This is the script to run in front of an investor - it's the
same HTTP surface a real Epicor tenant's webhook would call, just with
realistic sample data instead of a live client feed.

Deliberately does NOT call the manual /optimizer/{hub_id}/run-cycle
endpoint as the primary path - the point worth showing an investor is that
nothing has to be clicked. Order ingestion publishes an "order_held" event
(app/ingestion/router.py) that the Dispatch Optimizer already reacts to on
its own (app/optimizer/event_trigger.py). This script ingests, waits a
beat for that automatic cycle to finish, and then just confirms the order
left the hold queue - falling back to a manual trigger only if it's still
sitting there after the wait (belt-and-suspenders, not the headline path).

Run demo/seed_demo_data.py first (once per fresh stack) so the Hub/
Client/Shop/Driver this payload references actually exist.

Usage:
    python -m demo.send_demo_order
    DEMO_API_BASE_URL=http://localhost:8000 python -m demo.send_demo_order
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import settings
from demo.ids import CLIENT_ID, HUB_ID, SOURCE_SYSTEM

BASE_URL = os.environ.get("DEMO_API_BASE_URL", "http://localhost:8000")
PAYLOAD_PATH = Path(__file__).parent / "epicor_sample_order.json"
AUTO_DISPATCH_WAIT_SECONDS = 1.5  # margin over the <5s cycle budget for 1 order/1 driver


def _headers() -> dict:
    # Matches app/security.py's SharedSecretAuthMiddleware - only needed if
    # API_SHARED_SECRET is actually set for the stack being demoed against.
    if settings.api_shared_secret:
        return {"X-API-Key": settings.api_shared_secret}
    return {}


def _held_order_ids() -> list[str]:
    resp = httpx.get(f"{BASE_URL}/batch-queue/{HUB_ID}/held-orders", headers=_headers(), timeout=10)
    resp.raise_for_status()
    return [o["order_id"] for o in resp.json()]


def main() -> None:
    payload = json.loads(PAYLOAD_PATH.read_text())
    # Stamp a fresh timestamp every run so the demo doesn't look stale and
    # SLA hold-window math is computed against "now", not whenever this
    # file was written.
    payload["OrderDate"] = datetime.now(timezone.utc).isoformat()

    print(f"1. Sending Epicor order {payload['OrderNum']} ({payload['Description']!r})...")
    ingest_url = f"{BASE_URL}/ingestion/{HUB_ID}/{CLIENT_ID}/{SOURCE_SYSTEM}"
    resp = httpx.post(ingest_url, json=payload, headers=_headers(), timeout=10)
    resp.raise_for_status()
    ingested = resp.json()
    order_id = ingested["order_id"]
    print(f"   -> order_id={order_id}  sla_tier={ingested['sla_tier']}  "
          f"status={ingested['status']}  hold_deadline={ingested['hold_deadline']}")

    print(f"2. Waiting {AUTO_DISPATCH_WAIT_SECONDS}s for the Dispatch Optimizer's automatic "
          f"event-triggered cycle to run (no manual trigger needed)...")
    time.sleep(AUTO_DISPATCH_WAIT_SECONDS)

    if order_id not in _held_order_ids():
        print("   -> DISPATCHED: order left the hold queue automatically - the optimizer "
              "matched it to the available driver the moment it was released.")
    else:
        print("   -> Still held after the wait; forcing a cycle manually as a fallback...")
        resp = httpx.post(f"{BASE_URL}/optimizer/{HUB_ID}/run-cycle", headers=_headers(), timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result["assignments"]:
            a = result["assignments"][0]
            print(f"   -> ASSIGNED: driver {a['driver_id']} -> stop(s) {a['stop_ids']} "
                  f"(engine={result['engine']})")
        else:
            print(f"   -> Still not assigned. unassigned={result['unassigned_stop_ids']}. "
                  "Check that demo/seed_demo_data.py ran and the driver shows 'available' at "
                  f"GET {BASE_URL}/fleet/{HUB_ID}/drivers.")

    print()
    print(f"Open the dashboard (http://localhost:5173) to see Fleet Overview and Hold Queue "
          f"reflect this live, or hit GET {BASE_URL}/batch-queue/{HUB_ID}/held-orders directly.")


if __name__ == "__main__":
    main()
