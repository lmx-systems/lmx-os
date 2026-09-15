# Background location — the consent package

**v1.0 · 15 September 2026 · Sourabh**

Closes `ROADMAP_1.5.md` Phase 0 items **0.4** (driver-app access and
background-location consent negotiated into pilot terms — Matan) and **0.8**
(the "always" authorisation tier and its store justification — Sourabh +
Matan). Together they unblock **`DRV-2`**, which with `DRV-1` is *"the whole of
Phase 1's risk."*

**Lens:** investor. The geofence sensor is what produces the eligibility and
autonomy dataset; it closes a measurement gap, per the governing rule.

Jurisdiction: `ROADMAP_1.5.md` §2.2(a) decides *what we record*. This document
does not reopen that. It says how the same decision is stated to the three
audiences who have to accept it — Apple, Google, and the design partner's
drivers — and what has to change in the repo before any of them see it.

---

## 1. The one sentence

Everything below is one claim, said three ways:

> **We record when a driver arrives at and leaves a delivery address. We do not
> record where they are in between.**

That is not a softening of the ask. It is literally the design: §2.2(a) chose
geofence enter/exit events over a position breadcrumb because **we need the two
edges of a stop, not the path between them**. The existing 30-second position
pings (`F1`) stay foreground-only and are *not* the measurement — they draw the
ops map. If a reviewer, a lawyer, or a driver comes away believing we keep a
continuous trail, the ask has been described wrongly, not approved.

**The three statements must not drift apart.** That is why 0.4 and 0.8 are one
document: the App Store justification, the pilot clause, and the in-app screen
are the same sentence with different amounts of detail.

---

## 2. Why the easy answer doesn't work

Worth stating plainly, because it is the first question each audience asks.

**"Can't the driver just tap arrive and depart?"** They can, and today they do
— but a tap measures when someone remembered to tap. The design partner's
dispatch export is minute-resolution and **65.5% of stops compute to zero
dwell**; the one second-precision export shows those same stops really took
3–50 seconds. The whole of Phase 1 exists because no filter recovers that. A
human-triggered timestamp reproduces the defect we are trying to remove.

**"Can't you use the foreground pings you already have?"** No. They stop the
moment the driver backgrounds the app, which is most of a delivery. They also
have a 25-metre movement floor, so a driver standing at a counter emits
nothing — precisely the interval we are trying to measure.

**"Can't you compute it from the route?"** That is the modelled number we
already have and the reason gap 2 is open.

---

## 3. What actually changes in the app

Current state, verified in the repo rather than recalled:

| | Today | After `DRV-1`/`DRV-2` |
|---|---|---|
| `app.json` → `expo-location` | `isIosBackgroundLocationEnabled: false`, `isAndroidBackgroundLocationEnabled: false` | both `true` |
| iOS `Info.plist` | `NSLocationWhenInUseUsageDescription` only | adds `NSLocationAlwaysAndWhenInUseUsageDescription` |
| Android `permissions` | `ACCESS_COARSE_LOCATION`, `ACCESS_FINE_LOCATION` | adds `ACCESS_BACKGROUND_LOCATION` |
| `src/location/reportDriverLocation.ts` | 30s / 25m position pings, foreground | **unchanged** — still foreground, still the ops map |
| Geofence layer | does not exist | new: `startGeofencingAsync` + TaskManager, emits **stop events** |

The second-to-last row is the one to hold on to. We are not upgrading the
existing tracker; we are adding a second, narrower sensor beside it and leaving
the first one exactly as it is.

### 3.1 The platform constraints, and which is binding

- **iOS monitors at most 20 geofence regions per app.** The design partner's
  best driver runs 20.2 stops per route. `DRV-1` therefore needs **rolling
  registration** — hold the next N stops only, re-register as the route
  advances. §2.2(a) already says to design for this from the first commit;
  retrofitting means re-testing the whole sensor.
- **Android's geofencing limit is 100 per app per device** — comfortable. iOS
  is the binding constraint and sets the design.
- **Background geofence delivery on iOS requires the "always" tier.**
  When-in-use does not deliver region events once the app is backgrounded,
  which is the entire point.
- **Both platforms want the escalation, not the cold ask.** Request
  when-in-use first, run a shift, then ask for "always" with the reason
  on screen. A cold "always" prompt is the most common rejection cause and
  the most common driver refusal.

### 3.2 What blocks the build even after consent

**`A6` is not done.** `driver-app/eas.json` and `.github/workflows/eas-build.yml`
exist and are real, but **no Expo account or project exists**, so
`app.json` has no `extra.eas.projectId`. None of this can be tested on a device
until one `eas init` runs — which also unlocks `A1`'s push notifications.
Neither permission tier is verifiable in Expo Go.

So the honest sequence is: **0.4 and 0.8 unblock the work; `eas init` unblocks
testing it.** They are separate blockers and closing one does not close the
other.

---

## 4. Part A — the store submissions (0.8)

### 4.1 App Store review justification

Submitted in App Store Connect under the background-location review note. This
is the text a reviewer reads:

> LMX Driver is a delivery-dispatch app used by commercial delivery drivers on
> shift. It uses background location for one purpose: to record the moment a
> driver arrives at and departs from an assigned delivery address.
>
> The app registers a geofence around each stop on the driver's assigned route
> and records only the enter and exit events. It does not record a position
> trail in the background, and the app's foreground position reporting — used
> to show the driver on the dispatcher's map — is unchanged and remains
> foreground-only.
>
> This is required rather than convenient. Arrival and departure times are the
> measurement the product is built on: they determine whether a delivery met
> its committed time window and how long each stop actually takes. Requiring
> the driver to tap a button on arrival both measures the wrong thing (when
> they remembered to tap) and takes their attention at the least safe moment.
>
> Location is recorded only while the driver is on duty and only for stops on
> their own assigned route. It is not recorded off duty. Drivers are told this
> in the app before the permission is requested, and separately in the terms
> their employer agrees to.

### 4.2 Google Play declaration

**This is missing from 0.8 as written, and it is not optional.** Play requires
a separate background-location access declaration in Play Console *and* a
**prominent in-app disclosure shown before the runtime permission prompt** —
its own screen, not buried in a policy. The declaration reuses the text above;
the disclosure is §4.4 below, which satisfies both stores.

Treat Play as a peer of App Store review in 0.8, not a footnote.

### 4.3 Permission strings

iOS, `NSLocationAlwaysAndWhenInUseUsageDescription`:

> Records when you arrive at and leave each delivery on your route, so your
> arrival times are accurate without you having to tap anything. Your location
> is not recorded between stops, and never when you're off duty.

iOS, `NSLocationWhenInUseUsageDescription` — **revise the existing string**, so
the two read as one story rather than two unrelated asks:

> Shows dispatch where you are while you're on duty, so you're offered the
> deliveries nearest to you. Your location is not shared when you're off duty.

### 4.4 In-app disclosure screen

Shown before the "always" prompt — required by Play, and the difference between
a driver granting and refusing on iOS. Plain wording, no SLA jargon, consistent
with the driver app's existing voice:

> **Arrival times, without the tapping**
>
> LMX Driver can record when you arrive at and leave each delivery
> automatically, using the addresses already on your route.
>
> **What this records** — the time you arrive at a delivery address, and the
> time you leave it.
>
> **What it doesn't** — where you are between stops. Your route isn't traced,
> and nothing is recorded when you're off duty.
>
> **Why** — so your arrival times are right without you having to stop and tap
> a button in traffic, and so a delivery that ran late can be explained rather
> than argued about.
>
> You can turn this off at any time in your phone's settings. The app keeps
> working; you'll be back to marking arrivals by hand.
>
> [ Not now ]   [ Continue ]

That last paragraph is load-bearing and should survive review unedited. A
permission a driver cannot decline is a permission a labour regulator will read
as control, which cuts directly across `A10`'s classification work.

---

## 5. Part B — the pilot terms (0.4)

**Draft language for counsel, not legal advice.** It follows the pattern in
`LEGAL_BRIEF.md`: state the position, name what it depends on, leave the
undecided parts in brackets rather than guessing.

The structural fact that makes 0.4 hard: **the drivers are the design
partner's employees, not ours.** We cannot obtain their consent directly and
should not try to. The customer permits the app, and the customer carries the
employment relationship in which consent is meaningful.

### 5.1 Clause — driver application access

> **Driver application.** The Customer will make the LMX Driver application
> available to drivers performing deliveries under this agreement, on devices
> capable of running it, and will permit its use during working hours. LMX
> provides the application at no charge for the term.
>
> The Customer will procure that each driver is informed, before first use, of
> what the application records, as set out in the Driver Notice at Schedule
> [N]. The Customer confirms it has the standing under its own employment
> arrangements and privacy notices to permit this processing, and LMX will
> supply the Driver Notice text and any changes to it not less than [14] days
> before they take effect.

### 5.2 Clause — location recording

> **Location data.** The application records (a) the driver's position at
> intervals while the driver is on duty and the application is in the
> foreground, for dispatch and to display the driver on the Customer's
> operational map; and (b) the times at which a driver enters and leaves the
> vicinity of a delivery address on their assigned route, recorded
> automatically while on duty including when the application is not in the
> foreground.
>
> The application does not record the driver's position between deliveries
> while it is not in the foreground, and records nothing while the driver is
> off duty. LMX will not introduce continuous background position recording
> without the Customer's prior written agreement and not less than [30] days'
> notice.
>
> A driver may decline or withdraw the background permission through their
> device settings without losing access to the application. The Customer will
> not treat a driver's refusal as a disciplinary matter, and LMX will not
> report refusals other than as an aggregate count.

The third paragraph is the one to defend if it is pushed back on. It costs
measurement coverage and buys the position that this is a measurement
instrument rather than a supervision tool — which is what keeps `A10` and the
driver-visible scorecard (`W4`) defensible.

### 5.3 Clause — retention and access

> **Retention.** Position records are deleted [90] days after collection.
> Arrival and departure times form part of the delivery record and are retained
> with it for [seven years]. On termination, LMX will delete or return driver
> position records within [30] days at the Customer's election; delivery
> records are retained as business records.
>
> **Driver access.** On request from the Customer, LMX will provide a driver
> with the location records held about them, in a readable format, within [30]
> days.

### 5.4 Clause — data rights, deferred on purpose

**Do not write the pooling and training-rights clause here.** That is `0.2`,
it gates Phase 4 entirely, and it is Rich's with counsel. Bolting a narrow
version into the driver clause would create a second, weaker grant that the
real one then has to be reconciled against. One clause, one place.

If the pilot needs interim cover, the narrow form is *"LMX may use data arising
from deliveries performed under this agreement to operate and improve the
service provided to the Customer"* — which is operation, not pooling, and
deliberately does not reach Phase 4.

---

## 6. What this obliges us to change in the repo

Not optional follow-ups. Each is a place the published record currently says
something the new sensor makes untrue.

| # | Change | Why |
|---|---|---|
| 1 | ~~**`app/legal/content/privacy.md` §3** reads *"the app records your position at regular intervals."*~~ **Done.** §3 now separates the two sensors, states that between-stop position is not recorded in the background, and says a driver may refuse the permission without losing the app. Stop-event retention is a bracketed placeholder. | The policy is versioned and will be served. Shipping the permission against the old text would have made the published statement wrong on the exact point the permission turns on. See the publication-order row in §7. |
| 2 | **Retention decision: geofence stop events.** `driver_location_pings` is pruned at `location_ping_retention_days` (90) by `app/legal/retention.py`. Stop events are a different object — arrival and departure times are part of the delivery record, and §5.3 above proposes retaining them with it. **Someone has to decide**, and `app/legal/retention.py` needs the answer. | `LEGAL_BRIEF.md` §3 lists retention as a live decision. This adds one. A stated period nothing enforces is the defect that section exists to prevent. |
| 3 | **`app.json` permission strings** replaced with §4.3. | The current when-in-use string was written before this decision and reads as a different product. |
| 4 | Add the Play declaration to **0.8's definition of done** in `ROADMAP_1.5.md`. | 0.8 names App Store only. Play's requirement is independent and equally blocking. |

Items 1 and 2 are the ones with teeth: the privacy policy has a publication
test (`test_a_document_cannot_be_published_with_a_hole_still_in_it`) that will
refuse a bracketed placeholder, so the retention decision has to land before
the text can change.

---

## 7. What is still open after this document

| Open item | Owner | Blocks |
|---|---|---|
| Does the design partner accept §5.1–5.3 as drafted? | Matan | **0.4** — the whole of it |
| Counsel review of §5 | Rich + counsel | 0.4 sign-off |
| Retention period for stop events (§6, row 2) | Sourabh + Rich | `app/legal/retention.py`, and publication of the privacy policy |
| **Publication order.** §3 of the privacy policy now describes the geofence sensor, so the policy must not be published, or given an `effective:` date, until `DRV-2` actually ships. The stop-event placeholder blocks that today — **but it stops blocking the moment the retention row above is answered**, and nothing then checks whether the sensor exists. | Sourabh | publishing the privacy policy |
| Apple developer account and `eas init` | Sourabh | **`A6`** — and therefore any device testing of `DRV-1`/`DRV-2`, plus `A1` |
| Play Console account and background-location declaration | Sourabh | Android release |

**0.4 and 0.8 are not closed by this document.** It gives both the text they
were missing; closing them is a conversation with the design partner and two
store submissions. What it does close is the excuse that the wording didn't
exist yet.

---

## 8. Related

`ROADMAP_1.5.md` §2.2(a) (the decision), `DRV-1`–`DRV-5` (the build) ·
`ROADMAP.md` `A6` (EAS pipeline), `F1` (position pings), `W4` (driver
scorecard) · `docs/A10_1099_WORKER_AUTONOMY_RESEARCH.md` (why a declinable
permission matters) · `docs/LEGAL_BRIEF.md` (how a clause reaches publication)
· `app/legal/content/privacy.md` §3 · `app/legal/retention.py`
