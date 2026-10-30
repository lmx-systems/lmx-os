"""Run the scheduled sweeps locally, the way EventBridge does on a deployed stack.

    python -m demo.sweeps             # every 60 seconds until stopped
    python -m demo.sweeps --once      # one pass, then exit

`infra/aws/schedules.tf` calls `POST /internal/dispatch/run-all` and
`POST /internal/webhooks/deliver-pending` on a timer. Locally nothing does, and
the dispatch sweep is the only caller of the playbook (`DEC-8`): without it the
console's Home "about to" list and the Rules page stay empty for the whole demo,
however many orders need a call.

**The same calls with the same header, nothing more.** Each sweep is safe to
over-call, which is why the deployed schedule does not retry them and why this
can run beside `demo.run_full_loop` without coordinating with it: a cycle takes
the hub lock, so two never run for one hub at once.

Every 60 seconds rather than the deployed five minutes, because a demo is
shorter than a working day. The retention prune is left out: a demo has nothing
old enough to prune.

Needs `INTERNAL_API_TOKEN` set for the API, in `.env` (the runbook's setup
writes one). Without it every `/internal/*` route answers 404 on purpose.
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

from app.config import settings

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_INTERVAL_SECONDS = 60.0
SWEEP_PATHS = ("/internal/dispatch/run-all", "/internal/webhooks/deliver-pending")


def run_sweeps(http: httpx.Client, token: str) -> bool:
    """One pass over every sweep. False when any of them was refused."""
    ok = True
    for path in SWEEP_PATHS:
        response = http.post(path, headers={"X-LMX-Internal-Token": token})
        if response.status_code == 404:
            print(
                f"{path}: 404. The API has no INTERNAL_API_TOKEN, or a different "
                "one - put the same value in .env and restart the app."
            )
            ok = False
            continue
        response.raise_for_status()
        print(f"{path}: {response.json()}", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    token = settings.internal_api_token
    if not token:
        print("INTERNAL_API_TOKEN is not set. Add it to .env (see demo/INVESTOR_RUNBOOK.md).")
        return 1

    with httpx.Client(base_url=args.base_url, timeout=30.0) as http:
        try:
            while True:
                if not run_sweeps(http, token):
                    return 1
                if args.once:
                    return 0
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
