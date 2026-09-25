# The driver app, as it actually is

**What this is.** The layer with the least written about it and, until this
week, the least test coverage — five test files across 63 modules. It is also
where `DRV-1`'s measurement is produced, which makes it where Phase 1 succeeds
or quietly does not.

Written after two bugs turned up in the first two modules anybody read closely.
Every claim below is checkable against a named file.

---

## 1. What survives no signal, and what does not

The outbox (`src/offline/`) is the whole offline story. Five action types queue
to AsyncStorage, survive the app being killed, and flush when connectivity
returns.

| Action | Retry is safe because |
|---|---|
| `arrive` | idempotent on the server |
| `scan` | a count, not an increment |
| `complete` | `complete_stop` returns the existing result rather than a 409 |
| `flag` | idempotent on the server |
| `geofence` | the endpoint de-duplicates on `(stop, kind, occurred_at)` |
| `survey` | the server overwrites the same dock's columns; `profile_for` is keyed on the canonical dock, so a replay cannot make a second profile |

Backoff is `2s, 5s, 15s, 30s, 60s`, then 60s thereafter.

**What does not survive: position pings.** Deliberately. A ping is a sample of
a continuous signal, and replaying a twenty-minute-old fix on reconnect would
report where a driver *was* as where they are — worse than nothing for the
live-insertion decision that is the only thing wanting recency. The ops map
draws a stale fix hollow rather than hiding it.

### The failure that cost a whole shift

A 401 was classified as permanent, alongside genuine business-rule rejections.
Queue a day of stops in a dead zone, let the access token expire down there,
come back into signal: every item 401s at once, every one is marked
`permanentlyFailed`, `flush` skips them for ever and nothing clears the flag.

Two nested causes, both fixed: `/driver/auth/refresh` returned a token that
**nothing adopted** (the result was discarded), so expiry was certain rather
than unlucky; and 401 is now retryable, triggering one refresh before the next
pass. `isPermanentFailure` is a pure exported function with its own tests,
because the classification *is* the bug.

### The same failure has a second door, and it was unlatched

Treating a 401 as transient works **only while the driver can still
authenticate.** Log out was a bare `onPress={signOut}` — no confirmation, no
count — and signing out with queued work neither sends it nor discards it: it
strands it. The token goes, every subsequent flush 401s, and `refreshOnce` has
no session left to refresh with, so the queue retries against nothing until
somebody signs back in on that same phone.

`logOutWarning` (`src/utils/logOutWarning.ts`) is now in front of it, and says
nothing at all when the queue is empty — a dialog on every log out is one
drivers learn to dismiss, and it would stop working on the day it mattered.
The same constraint is why `DevicesScreen` shows the current phone without a
sign-out button: revoking the session your own queue drains through is the
identical bug wearing a different name.

---

## 2. What is best-effort on purpose

Six modules say "best-effort" and mean it. The shared rule: **a measurement may
fail; a delivery may not.**

- Location permission denied → no ops map, no automatic arrivals, every
  delivery still completable by tapping.
- Push registration failed → job offers arrive when the app is opened.
- Geofence registration failed → arrivals fall back to taps.
- A payout or an SMS failing must never roll back a completed delivery.

The one deliberate exception is in the backend, not here:
`record_delivery_outcomes` runs *inside* `complete_stop`'s transaction, because
a delivered order with no outcome row is the state `REC-3` exists to prevent.

---

## 3. The sensors, and what turns them off

| Constant | Value | Why |
|---|---|---|
| `GEOFENCE_RADIUS_M` | 75 | **A guess.** `measure_geofence_calibration` is what checks it, and now reports into `/operations/record-health` |
| `MAX_MONITORED_REGIONS` | 18 | iOS monitors 20 per *app* and drops the excess without saying which. The margin is for `DRV-3`'s warehouse fence |
| `LOCATION_PING_INTERVAL_MS` | 30,000 | The optimizer re-plans on events, not a timer, so a fresher fix buys nothing |
| `MINIMUM_DISTANCE_M` | 25 | A driver at a counter would otherwise emit a stream of identical fixes |

**Permission can go away mid-shift and used to go unnoticed.** It was checked
when the watcher started and when geofences registered, and never again — so a
driver who revoked it in Settings at 10am left the app holding state that
claimed a sensor it no longer had, and `DRV-1`'s second-precision dwell quietly
degraded to tap-grade for the shift. `useLocationDegradation` re-checks on
foreground, tears down what cannot work, and says what stopped.

It never re-requests. The "always" tier is asked for once, on a screen that
explains it first (`docs/BACKGROUND_LOCATION_CONSENT.md` §4.4); a cold prompt
after a deliberate refusal is how an app earns a permanent denial.

---

## 4. What is tested, and what that is worth

Five files, 48 tests. All of it is **pure logic that decides something**, and
none of it is a screen:

| Module | What a bug there would look like |
|---|---|
| `geofenceWindow` | a dock that quietly never records an arrival |
| `outboxManager.isPermanentFailure` | a shift's stop events discarded |
| `permissionState` | the app claiming a sensor it does not have |
| `stopStatus` | a stop a driver cannot finish |
| `applyOptimistic` | a button that does nothing, pressed twice |

**Screens are untested**, and that is a choice rather than an oversight for
now: they need heavy mocking and the return per test is much lower. It is still
a real gap — `StopDetailScreen` is 310 lines and owns proof of delivery.

### The two bugs reading found

Both in the offline path, both from the same cause — two modules restating one
condition and drifting.

`primaryActionForStop` offered "Arrived" from `pending` **and** `en_route`;
`applyPendingToStop` applied it only from `pending`. A stop reaches `en_route`
on its own, so tapping Arrived there while offline did nothing visible, and the
natural response to a dead button is to press it again. They share
`isBeforeArrival` now.

And a **pickup** showed a button reading "Confirm delivery" — at a shop,
loading the van, before anything had been delivered to anybody.

---

## 5. What is not built

- ~~**`DRV-3`, the warehouse geofence.**~~ **Built** (`bb8e101`, PR #96). This
  bullet was stale for over a week. `hub_geofence_events` is the separate table
  the old text said was needed, and the hub region rides in the stop set behind
  a `hub:` identifier prefix — the 18-stop window always left the slot.
- **`DRV-5`'s battery clause.** "Under 4% per 8-hour shift with background on"
  is measured on a real phone over a real shift and cannot be verified from a
  repository. The permission half is done.
- **Background position.** Foreground-only on purpose: the iOS "always" tier
  needs an App Store justification and a real developer account
  (`docs/ROADMAP.md` A6). The practical consequence is honest and worth knowing
  — position stops updating when the driver backgrounds the app, and job offers
  still arrive by push.

---

## 6. If you are about to change something here

The offline path is load-bearing in a way it was not six months ago: a queued
action can now sit unsent for a whole shift and still go through. Two rules
that earned themselves:

**Do not restate a condition another module owns.** Both bugs above were that.

**A retry classification is a decision, not a detail.** "Which failures are
permanent" decided whether a day's work survived, and it was one expression
inside a `catch`.
