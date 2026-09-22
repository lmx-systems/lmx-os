"""The whole pipeline in one command: a CSV lands, a driver delivers it.

    python -m demo.seed_demo_data
    python -m demo.run_full_loop

`send_demo_order.py` stops at "dispatched", which is where the interesting
half begins. This carries on: it drops a manifest the way a distributor
actually would, waits for the optimizer to offer the work, then plays the
driver - accepts, arrives, scans, records a geofence crossing, and delivers -
so the run ends with an order in `delivered` and a route in `completed`.

**Everything it touches is a real endpoint over HTTP.** No internal function
calls and no database writes of its own. If this script passes, the same calls
work from the driver app, because they are the same calls.

**It needs no external service.** Without `GOOGLE_CLOUD_PROJECT_ID` the
optimizer falls back to its nearest-neighbour stub, and without Twilio the OTP
endpoint returns the code in its response. Both are deliberate
unconfigured-to-stub paths, not test seams.

What it does not demonstrate, and should not be described as demonstrating: a
live routing solve (the stub does not model time), an SMS to a shop or a
recipient, or any real geography - the addresses are Austin, and the design
partner is not.
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

from demo.ids import CLIENT_ID, DRIVER_PHONE, HUB_ID, SHOP_ID

DEFAULT_BASE_URL = "http://localhost:8000"

CLIENT_EMAIL = "demo-client@example.com"
CLIENT_PASSWORD = "demo-password-123"
OPS_EMAIL = "demo@lmxit.com"
OPS_PASSWORD = "demo-password"

# `Priority` is the column the manifest parser reads for per-row urgency.
# HOT SHOT holds for two minutes; RUSH is T1 at eight and plain rows are T2 at
# ninety. Two minutes is long enough to watch the hold do its job and short
# enough to sit through, which is the trade a live demo actually faces.
# Addresses are quoted because they contain commas. An unquoted address in a
# file with other columns shifts every field after it, which the parser then
# correctly rejects - a realistic failure, but not the one this demo is for.
MANIFEST_CSV = """Ship To Address,Contact Name,Priority
"1200 E 6th St, Austin, TX 78702",J. Rivera,HOT SHOT
"500 Congress Ave, Austin, TX 78701",M. Chen,HOT SHOT
"""


# Presenter mode. Zero by default, so CI and a rehearsal run at full speed and
# only a live audience pays for the pauses.
_PACE = 0.0


def step(number: int, text: str, *, look_at: str | None = None) -> None:
    """One beat of the demo, and where to point while it happens.

    `look_at` exists because the interesting thing is rarely in the terminal.
    The terminal is the narration; the product is the three screens beside it,
    and a presenter reading output aloud while the audience watches the wrong
    window is the failure this avoids.
    """
    print(f"\n{number}. {text}")
    if look_at:
        print(f"   [ on screen: {look_at} ]")
    _beat()


def _beat(multiplier: float = 1.0) -> None:
    """Let an audience catch up. A no-op unless --pace was asked for."""
    if _PACE:
        time.sleep(_PACE * multiplier)


def detail(text: str) -> None:
    print(f"   -> {text}")
    _beat(0.35)


class DemoFailed(Exception):
    """Something the demo depends on is not set up. Says what to do about it."""


def _login_client(http: httpx.Client) -> str:
    response = http.post(
        "/client/auth/login", json={"email": CLIENT_EMAIL, "password": CLIENT_PASSWORD}
    )
    if response.status_code != 200:
        raise DemoFailed(
            f"Could not log in as '{CLIENT_EMAIL}' ({response.status_code}). Create it:\n"
            f"  python -m scripts.create_client_user --client-id {CLIENT_ID} "
            f'--email {CLIENT_EMAIL} --password "{CLIENT_PASSWORD}" '
            f'--name "Demo Client" --role admin'
        )
    return response.json()["access_token"]


def run(base_url: str, poll_seconds: float) -> int:
    with httpx.Client(base_url=base_url, timeout=30.0) as http:
        try:
            http.get("/health").raise_for_status()
        except Exception as unreachable:
            raise DemoFailed(
                f"No LMX OS at {base_url} - start the stack first "
                f"(`docker compose up -d`). {unreachable}"
            ) from unreachable

        step(1, "A distributor drops a CSV manifest (LMX Link, T3)",
             look_at="client portal :5174 - Orders appear as the file is parsed")
        client_token = _login_client(http)
        upload = http.post(
            "/client/orders/manifest",
            headers={"Authorization": f"Bearer {client_token}"},
            files={"file": ("manifest.csv", io.BytesIO(MANIFEST_CSV.encode()), "text/csv")},
            data={"deadline": "today", "pickup_shop_id": str(SHOP_ID)},
        )
        upload.raise_for_status()
        result = upload.json()
        detail(f"{result['accepted']} accepted, {result['failed']} rejected")
        detail(f"columns matched: {result['column_mapping']}")
        for row in result["results"]:
            if row["order"]:
                detail(
                    f"line {row['line_number']}: {row['order']['reference']} "
                    f"tier={row['order']['sla_tier']} status={row['order']['status']}"
                )
            else:
                detail(f"line {row['line_number']}: REJECTED - {row['error']}")
        if result["accepted"] == 0:
            raise DemoFailed("Nothing was accepted, so there is nothing to deliver.")

        step(2, "The SLA engine held them; the optimizer offers the work",
             look_at="ops console :5173 - Hold Queue, then the order leaving it")
        detail("held rather than dispatched instantly - that hold is the product")
        driver_token = _sign_in_driver(http)
        clocked_on = _clock_on(http, driver_token)
        offers = _wait_for_offer(http, driver_token, _ops_token(http), poll_seconds)
        detail(f"{len(offers)} offer(s) reached the driver with no button pressed")

        step(3, "The driver accepts",
             look_at="the handset - the offer arrives without anybody pressing anything")
        accepted = http.post(
            f"/driver/offers/{offers[0]['offer_id']}/accept",
            headers={"Authorization": f"Bearer {driver_token}"},
        )
        accepted.raise_for_status()
        route = accepted.json()
        detail(f"route {route['route_id']} with {len(route['stops'])} stops")

        step(4, "The driver runs the route")
        _drive_route(http, driver_token)

        step(5, "Where it ended up")
        if clocked_on:
            _clock_off(http, driver_token)
        _report(http, driver_token, clocked_on)
    return 0


def _ops_token(http: httpx.Client) -> str | None:
    """The dispatcher's own login, used only to nudge a cycle. Optional.

    Without it the demo still works, it just waits for some other event to
    trigger the cycle - which in a quiet local stack may be never.
    """
    response = http.post(
        "/ops/auth/login", json={"email": OPS_EMAIL, "password": OPS_PASSWORD}
    )
    if response.status_code != 200:
        print(
            f"   (no ops login - create one for a faster demo:\n"
            f"    python -m scripts.create_ops_user --email {OPS_EMAIL} "
            f'--password "{OPS_PASSWORD}" --name "Demo" --role admin)'
        )
        return None
    return response.json()["access_token"]


def _sign_in_driver(http: httpx.Client) -> str:
    requested = http.post("/driver/auth/request-otp", json={"phone": DRIVER_PHONE})
    requested.raise_for_status()
    code = requested.json().get("debug_code")
    if not code:
        raise DemoFailed(
            "The OTP was sent by SMS rather than returned, so this script cannot read "
            "it. Unset TWILIO_ACCOUNT_SID to run the demo without a phone."
        )
    verified = http.post(
        "/driver/auth/verify-otp",
        json={"phone": DRIVER_PHONE, "code": code, "device_id": "demo-loop"},
    )
    verified.raise_for_status()
    return verified.json()["access_token"]


def _wait_for_offer(
    http: httpx.Client, token: str, ops_token: str | None, poll_seconds: float
) -> list[dict]:
    """Wait out the hold, nudging a dispatch cycle until the work is offered.

    **Why the nudge is here and is not a cheat.** Ingesting an order publishes
    an event that runs one cycle immediately - which is correct, and which finds
    nothing, because the order is still inside its hold window. Something has to
    look again once the hold expires. In production that is Cloud Scheduler
    calling `POST /internal/dispatch/run-all`; locally nothing does, so the demo
    plays that role. It cannot dispatch anything the hold has not released, so
    it shortens no window and skips no step.
    """
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.monotonic() + 240
    announced = False
    while time.monotonic() < deadline:
        offers = http.get("/driver/me/offers", headers=headers)
        offers.raise_for_status()
        if offers.json():
            return offers.json()
        if ops_token:
            http.post(
                f"/optimizer/{HUB_ID}/run-cycle",
                headers={"Authorization": f"Bearer {ops_token}"},
            )
        if not announced:
            detail("inside the hold window - waiting for it to release (up to 2 min)")
            announced = True
        time.sleep(poll_seconds)
    raise DemoFailed(
        "No offer reached the driver in four minutes. Either the driver is not "
        "`available` (re-run `python -m demo.seed_demo_data`), or nothing is "
        "releasing from the hold queue - check the dashboard's hold queue."
    )


def _drive_route(http: httpx.Client, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    route = http.get("/driver/me/route", headers=headers)
    route.raise_for_status()
    stops = sorted(route.json()["stops"], key=lambda s: s["sequence"])

    for stop in stops:
        stop_id, kind = stop["stop_id"], stop["stop_type"]

        # A geofence crossing, as the phone would have sent it (DRV-1). Recorded
        # BEFORE the tap and a little earlier in time, which is the whole point:
        # the fence fires on approach, the tap happens when somebody remembers.
        crossed_at = datetime.now(timezone.utc) - timedelta(seconds=20)
        http.post(
            f"/driver/stops/{stop_id}/geofence-events",
            headers=headers,
            json={"events": [{"kind": "enter", "occurred_at": crossed_at.isoformat()}]},
        ).raise_for_status()

        http.post(f"/driver/stops/{stop_id}/arrive", headers=headers).raise_for_status()
        if kind == "pickup":
            http.post(
                f"/driver/stops/{stop_id}/scan",
                headers=headers,
                json={"scanned_count": stop.get("parcel_count", 1)},
            ).raise_for_status()

        photo_url = _capture_pod_photo(http, headers, stop_id, kind)
        http.post(
            f"/driver/stops/{stop_id}/complete",
            headers=headers,
            json={"method": "photo", "photo_url": photo_url},
        ).raise_for_status()

        left_at = datetime.now(timezone.utc)
        http.post(
            f"/driver/stops/{stop_id}/geofence-events",
            headers=headers,
            json={"events": [{"kind": "exit", "occurred_at": left_at.isoformat()}]},
        ).raise_for_status()
        detail(f"{kind} {stop_id[:8]}: crossed, arrived, completed")


def _capture_pod_photo(http: httpx.Client, headers: dict, stop_id: str, kind: str) -> str:
    """Take a proof-of-delivery photo the way the handset does.

    This used to post `https://example.invalid/pod.jpg` - a placeholder that
    made the run pass and made "delivered, with proof" undemonstrable, because
    there was no image anywhere to look at.

    It now walks the real two-step path: ask for an upload URL, PUT the bytes,
    submit the URL that comes back. Which backend serves it is not this script's
    business - with `PHOTO_UPLOAD_BUCKET` the PUT goes to S3, with
    `PHOTO_STORAGE_DIR` it goes to this API's own `/media`, and with neither the
    stub says `requires_upload=False` and there is nothing to upload. The first
    two put a real photo on the ops console and the recipient's tracking page.
    """
    minted = http.post(
        f"/driver/stops/{stop_id}/upload-url",
        headers=headers,
        json={"kind": "photo", "content_type": "image/jpeg"},
    )
    minted.raise_for_status()
    upload = minted.json()

    if not upload["requires_upload"]:
        # No storage configured. Say so once rather than letting somebody
        # discover it in front of an audience by clicking a dead image.
        detail("no photo storage configured - POD is a marker, not an image")
        return upload["final_url"]

    http.put(
        upload["upload_url"],
        content=_pod_jpeg(stop_id, kind),
        headers={**headers, "Content-Type": "image/jpeg"},
    ).raise_for_status()
    return upload["final_url"]


def _pod_jpeg(stop_id: str, kind: str) -> bytes:
    """A stand-in doorstep photo, labelled so nobody mistakes it for one.

    The script is playing a driver who has no camera. A photo that *looked*
    real would be the one thing in this demo pretending to be something it is
    not, so it says what it is on its face. A real handset running the app
    takes a real photo through this same endpoint.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 480), (28, 32, 38))
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, 624, 464), outline=(90, 170, 130), width=3)
    draw.text((40, 200), "SIMULATED PROOF OF DELIVERY", fill=(232, 236, 240))
    draw.text((40, 230), f"{kind} stop {stop_id[:8]}", fill=(150, 160, 170))
    draw.text((40, 260), "captured by demo/run_full_loop.py", fill=(150, 160, 170))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def _clock_on(http: httpx.Client, token: str) -> bool:
    """Put the driver on the clock through the endpoint that logs it.

    `seed_demo_data` sets the Redis fleet state directly, which is enough for
    the optimizer to offer work and leaves no `driver_shift_event` behind. That
    gap was invisible until `REC-2` went looking for a wage to attribute and
    found a day of drops with no shift under it.

    **A 409 here is the demo working, not failing.** `R4`'s compliance gate
    refuses to put a driver on shift until every document is on file, reviewed
    by an ops user and unexpired - and the seeded demo driver has none. So the
    run continues and says what the refusal costs: with no shift log, the day
    has no wage to attribute and `app/record/cost.py` will report that rather
    than invent one.
    """
    response = http.post(
        "/driver/me/state",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "available"},
    )
    if response.status_code == 409:
        detail(f"R4 refused to clock the driver on: {response.json().get('detail')}")
        detail("so this run records no shift, and REC-2 will have no wage to cost")
        return False
    response.raise_for_status()
    detail("driver clocked on - the shift log is what REC-2 costs the day against")
    return True


def _clock_off(http: httpx.Client, token: str) -> None:
    http.post(
        "/driver/me/state",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "off_shift"},
    ).raise_for_status()


def _report(http: httpx.Client, token: str, clocked_on: bool = False) -> None:
    route = http.get("/driver/me/route", headers={"Authorization": f"Bearer {token}"})
    if route.status_code == 200 and route.json():
        detail("the driver still has an active route - some stop did not complete")
    else:
        detail("the driver's route is finished")
    costing = (
        "and a shift log to cost the day against (app/record/cost.py)"
        if clocked_on
        else (
            "but no shift log, so REC-2 has no wage to attribute - seed reviewed\n"
            "driver documents and R4 will let the clock start"
        )
    )
    print(
        "\nThe orders are delivered, the route is completed, every stop has a\n"
        f"machine-recorded arrival beside the driver's tap, {costing}.\n"
        "Dashboard:\n"
        "  http://localhost:5173    (ops)\n"
        "  http://localhost:5174    (client portal)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--poll-seconds", type=float, default=1.5)
    parser.add_argument(
        "--pace",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "pause this long between beats so an audience can follow. 2.5 is "
            "about right in front of people; the default 0 is for rehearsal "
            "and CI"
        ),
    )
    args = parser.parse_args()
    global _PACE
    _PACE = args.pace
    try:
        return run(args.base_url, args.poll_seconds)
    except DemoFailed as failure:
        print(f"\n{failure}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
