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

## 6. If something breaks

| Symptom | Cause |
|---|---|
| Handset cannot reach anything | `MEDIA_BASE_URL`/server address is `localhost`, or wifi client isolation |
| POD photo is a broken image | `PHOTO_STORAGE_DIR` unset — the stub issues a `local-capture://` marker and stores nothing. The page renders nothing rather than a broken frame, so you will see a missing photo, not an error |
| `R4 refused to clock the driver on` | **Working as designed.** The compliance gate refuses a driver with no reviewed documents. The run continues and says what the refusal costs. It is a good thing to be asked about |
| Nothing was accepted from the manifest | The seeded shop id changed — re-run `seed_demo_data` |

Re-running the loop sends fresh orders; nothing needs resetting between runs.
