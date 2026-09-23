# The investor demo: one screen, a real order, a real photo

A CSV lands the way a distributor sends one. The SLA engine classifies it, the
hold queue holds it, the optimizer offers it, a driver on a real handset accepts
it, walks into a geofence, photographs a doorstep, and the recipient's tracking
page shows that photo.

Every call is a real HTTP endpoint. Nothing here is a mock, a fixture or a
recording.

---

## 1. What the audience sees

Four surfaces, three of them in a browser and one in your hand.

| Where | What it is | What to point at |
|---|---|---|
| `:5174` | **Client portal** — the distributor's own view | the manifest being parsed, orders appearing |
| `:5173` | **Ops console** — the dispatcher's board | the Hold Queue, then the order leaving it |
| `:5174/track/<token>` | **The recipient's page** — no login, just a link | live status, then the delivery photo |
| the handset | **The driver app** | the offer arriving, the camera, the tap |

Lay the three browser windows out beforehand. Mirror the phone (QuickTime →
File → New Movie Recording → select the iPhone) into the fourth quadrant.

**Do not build a combined screen.** These four *are* the product. A unified
demo view would be showing something that does not ship.

---

## 2. Setup, once

```bash
docker compose up -d

# Local proof-of-delivery photos. MEDIA_BASE_URL must be the address the PHONE
# can reach - on a handset `localhost` means the handset, which is the single
# most likely way this demo fails.
export PHOTO_STORAGE_DIR=/tmp/lmx-pod
export MEDIA_BASE_URL=http://$(ipconfig getifaddr en0):8000
export ENVIRONMENT=development

# The client portal is blocked without this. DASHBOARD_CORS_ORIGINS defaults to
# the ops console alone, so every request the portal makes fails preflight -
# found by standing the demo up, not by any test, because the tests call route
# functions and never cross CORS.
export DASHBOARD_CORS_ORIGINS="http://localhost:5173,http://localhost:5174"

docker compose exec app python -m demo.seed_demo_data
docker compose exec app python -m scripts.create_ops_user \
    --email demo@lmxit.com --password "demo-password" --name "Demo" --role admin
docker compose exec app python -m scripts.create_client_user \
    --client-id <CLIENT_ID printed by the seeder> \
    --email demo-client@example.com --password "demo-password-123" \
    --name "Demo Client" --role admin
```

**The handset:** same wifi, then set the server address in the app's Profile
screen to `http://<your LAN ip>:8000`. Sign in as the seeded driver; the OTP
comes back in the response because Twilio is unconfigured.

### Which build, and what each one can show

This decides whether the audience sees a delivery app or a measurement system.

| | Expo Go | iOS Simulator | Dev / preview build |
|---|---|---|---|
| Sign in, route, arrive by tapping | yes | yes | yes |
| **Real POD photo from a camera** | yes | **no camera** | yes |
| POD by signature | yes | yes | yes |
| **Automatic arrival on a geofence (`DRV-1`)** | **no** | **yes** (simulate location) | yes |
| **Push to a locked device** | not from this project | no | yes |
| Needs an Apple Developer account | no | **no** | iOS only |
| On the same screen as the browser | no | **yes** | no |

`docs/BACKGROUND_LOCATION_CONSENT.md` §101 is blunt about the first: *neither
permission tier is verifiable in Expo Go.* And geofencing is not a nice-to-have
here — it is the measurement the product rests on. On Expo Go you tap Arrived
and say what the built version does, which is honest and much weaker.

### The iOS Simulator, which needs no Apple account at all

```bash
cd driver-app && npx eas build --profile simulator --platform ios
```

`"simulator": true` produces an **unsigned** `.app`, so none of phase `0.8`'s
Apple Developer enrolment applies. Download the artifact, then:

```bash
tar -xzf ~/Downloads/build-*.tar.gz
xcrun simctl boot "iPhone 16 Pro" ; open -a Simulator
xcrun simctl install booted LMXDriver.app
```

**`localhost` works here.** A simulator shares the Mac's network, so the
build-time default `http://localhost:8000` reaches the API and you do not touch
the Profile screen at all - one less thing to get wrong.

**This is how you show the geofence without hardware.** `Features ▸ Location ▸
Custom Location…`, then put the driver on a stop:

| Stop | Latitude | Longitude |
|---|---|---|
| Pickup (the shop) | `30.2729` | `-97.7513` |
| Drop — 500 Congress Ave | `30.267483` | `-97.743622` |
| Drop — 1200 E 6th St | `30.264642` | `-97.730218` |

The fence is 75 m (`GEOFENCE_RADIUS_M`), so set the location a few hundred
metres away first and then move it onto the stop — a driver already standing
inside a region when it is registered does not generate a crossing.

**What a simulator cannot do is take a photograph.** `PhotoCaptureModal` uses
`expo-camera`, and there is no camera. Use the **signature** pad instead: it
works with a trackpad, `CompleteStopBody` has always accepted it, and the
recipient's page renders it captioned *"Signed for at the door"*.

**Android builds today; iOS on a real device does not.**

```bash
cd driver-app && npx eas build --profile development --platform android
```

The `development` profile already exists in `eas.json`, and `app.json` carries a
real `extra.eas.projectId` — so the Expo side is done. An **iOS** dev build
additionally needs an Apple Developer account for device provisioning, which is
the same thing phase `0.8` is still open on. So if the only handset is an
iPhone, the geofence beat is blocked on that account and not on any code here.

**Push is available and switched off.** `EXPO_PUSH_ENABLED=false` by default and
nothing else stands in the way — the client-side blocker `A1` described is gone.
Turn it on for the demo if you have the dev build; the offer landing on a locked
phone is the strongest thirty seconds available to you.

**Rehearse it once end to end on the actual network you will present on.** The
LAN address is the fragile part, and a conference wifi with client isolation
will break the handset half while everything else keeps working.

---

## 3. The run

```bash
# Rehearsal, full speed:
docker compose exec app python -m demo.run_full_loop

# Live, paced for an audience:
docker compose exec app python -m demo.run_full_loop --pace 2.5
```

`--pace` inserts a beat between steps and prints `[ on screen: ... ]` telling
you where to look. The terminal is the narration; the product is the windows
beside it.

**The last beat needs one more command.** A tracking token is disclosed in
exactly one place — the SMS — and Twilio is stubbed, so nothing tells you the
URL:

```bash
docker compose exec app python -m demo.tracking_links
```

That reads the database directly, which nothing else in `demo/` does. The link
is a capability: anyone holding it sees the delivery photo, so no API hands one
out, not even to the client who owns the order. In front of a customer the
recipient gets it by text.

**Open it last.** It is the beat an investor recognises without explanation —
they have received one of these from a courier — and it is the only screen in
the demo showing the photo the driver just took.

**To drive the driver half from the real phone instead of the script**, stop
after step 2 and do it by hand on the handset: accept the offer, tap Arrived,
photograph something, confirm. The script's driver and the app's driver are the
same endpoints — that is the point of it.

---

## 4. The two-minute hold

`HOT SHOT` holds for two minutes and the run spends most of its wall-clock
there. That silence is not dead air to apologise for — **it is the product.**
Everything else in this demo is plumbing that any courier has. The hold is the
thing that decides whether two drops share a van.

Say what is happening: the order is sitting in the queue because a second one
going the same way may still arrive, and the engine is weighing the deadline
against the chance of a cluster mate. Then show it leave the queue.

If the room will not take two minutes, send a `RUSH` row instead of `HOT SHOT`
— but say you shortened it.

---

## 5. What this does not show, and say so before you are asked

**The routing is not a live solve.** Without `GOOGLE_CLOUD_PROJECT_ID` the
optimizer uses a nearest-neighbour stub that does not model time, so sequencing
and ETAs are not what Google would return (`DEC-3`/`E1`). The hold, the
batching decision and the dispatch are real; the road network is not.

**No SMS goes anywhere.** Shop and recipient notifications hit the Twilio stub.

**The addresses are Austin and the design partner is not.** Everything in
`demo/` is invented, deliberately in a state the partner has no presence in, so
a synthetic row can never be mistaken for a real one.

**The scripted driver's photo says so on its face.** When `run_full_loop` plays
the driver it uploads an image captioned *SIMULATED PROOF OF DELIVERY*, because
a convincing fake doorstep is the one thing in this demo that would be
pretending. A real handset takes a real photo through the same endpoint.

**Photos are on local disk.** `PHOTO_STORAGE_DIR` is a development backend and
the code refuses to run it outside development — local disk loses every photo
on redeploy, and a POD photo is evidence in a dispute. Production wants
`PHOTO_UPLOAD_BUCKET`.

**A tracking link is a capability.** Anyone holding it sees the photo; the
unguessable token is what stands in for a login, on this backend and on S3
alike.

---

## 6. What the rehearsal caught

This runbook was written from the code and then run against a live stack, which
found three things the entire test suite did not. All three are fixed; they are
recorded because they are the shape of what a rehearsal is for.

**The delivery photo would not have loaded.** `GET /media/...` sat behind
`OpsUserAuthMiddleware` and answered `401`, so the `<img>` on the recipient's
page would have shown nothing. Every test calls route functions directly and
never crosses middleware, so 2,254 of them stayed green. The upload now lives
under `/driver` and the fetch under `/public` — each in the prefix whose
exemption rationale actually covers it, rather than widening the exemption list
for a GET that does not authenticate itself.

**A CSV manifest could not carry a recipient phone.** The parser had no such
column, `send_tracking_link_to_recipient` mints a token only when there is a
number to text, and so **the CSV path — LMX Link's whole premise — could never
produce a tracking page for anything.** The demo's delivered order had a null
token and the recipient's page could not be opened at all.

**And the phone was dropped halfway.** `upload_order_manifest` delegates to the
batch path, and `ClientOrderBatchRow` had no phone field — Pydantic accepted the
keyword and discarded it, so the wiring looked complete and the order still had
no number.

The lesson worth keeping: **rehearse on a live stack, not against the suite.**
Tests here call functions, not URLs.

## 7. If something breaks

| Symptom | Cause |
|---|---|
| Handset cannot reach anything | `MEDIA_BASE_URL`/server address is `localhost`, or wifi client isolation |
| POD photo is a broken image | `PHOTO_STORAGE_DIR` unset — the stub issues a `local-capture://` marker and stores nothing. The page renders nothing rather than a broken frame, so you will see a missing photo, not an error |
| `R4 refused to clock the driver on` | **Working as designed.** The compliance gate refuses a driver with no reviewed documents. The run continues and says what the refusal costs. It is a good thing to be asked about |
| Nothing was accepted from the manifest | The seeded shop id changed — re-run `seed_demo_data` |
| Client portal shows errors on every action | `DASHBOARD_CORS_ORIGINS` does not include `http://localhost:5174` |
| Ops board cluttered with old stops | A previous run. `python -m demo.reset --confirm`, then re-seed |

**Runs accumulate, and this matters between your rehearsal and the real thing.**
New orders join the driver's *existing* active route rather than starting a new
one, so a second run leaves the first run's stops on the board. After three
rehearsal runs the demo driver had 44 stops, most of them pending — which reads
badly on the ops console in front of an audience.

Between the rehearsal and the live run:

```bash
docker compose exec app python -m demo.reset            # counts what it would delete
docker compose exec app python -m demo.reset --confirm  # deletes it
docker compose exec app python -m demo.seed_demo_data   # driver back on shift
```

It clears the demo hub's orders and the demo driver's routes and stops, and
keeps the hub, client, shop, driver and both logins - so you do not redo §2. Dry
by default, and it takes no hub argument, so it can only ever touch the demo
hub's own id.

The loop reports the outstanding count rather than claiming the route finished,
so you will see it if you forget.
