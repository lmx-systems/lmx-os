# Route Optimization access — the request to hand to whoever owns the Google Cloud org

> **This is a hand-over document, not a runbook.** Its job is to let someone who has
> never seen this repo grant the right thing in two commands, without a conversation
> first. Everything here is quoted from `scripts/verify_route_optimization.py`'s own
> docstring, which is the authority; if the two ever disagree, the script is right.

Roadmap item `E1`. The optimizer client (`app/optimizer/google_routes_client.py`) has
been complete-looking and unverified for months — it has never made a live
`optimizeTours` call. This is the access needed to make exactly one.

---

## The ask

1. The **Route Optimization API** enabled on a project with billing active.
2. **`roles/cloudoptimization.user`** granted to the engineer running the check.
3. The **project id**, so it can go in `GOOGLE_CLOUD_PROJECT_ID`.

That is the whole request. Two commands on their side, one API call on ours.

## Why the Maps key we already hold does not cover this

We have a Google Maps Platform key and it is in active use — `L12` moved geocoding onto
it, and it is the only third-party client in this repo that has made a real call.

**Route Optimization is IAM-gated rather than an API-key product.** `GOOGLE_MAPS_API_KEY`
is for the other Maps Platform APIs and will not work here, and no amount of enabling
against that key will change it. This needs Application Default Credentials with the
`cloud-platform` scope, which is the only reason a role grant is involved at all.

Worth saying out loud in the request itself: someone who sees "we already have a Google
Maps key" will reasonably assume this is handled.

## Do not create a service-account key for this

It is the path of least resistance for a platform owner and it is the wrong one. A
downloaded JSON key is a long-lived credential that ends up in a backup or a commit, and
**none is needed here** — ADC covers the local run, and Cloud Run uses its own service
account via workload identity with the same role and no key file at all.
`GOOGLE_APPLICATION_CREDENTIALS` remains supported for the case where a key genuinely is
the only option. This is not that case.

## The commands

On the project — the part we need from them:

```
gcloud services enable routeoptimization.googleapis.com --project=PROJECT_ID

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="user:ENGINEER@lmxit.com" \
  --role="roles/cloudoptimization.user"
```

Locally — ours, listed so the whole picture is visible:

```
brew install --cask google-cloud-sdk
gcloud auth login
gcloud auth application-default login
gcloud auth application-default set-quota-project PROJECT_ID
```

**That last line is the one people miss.** ADC user credentials bill against a quota
project, and without one the call fails with an error that does not obviously say so. It
is the most likely reason a correctly-granted role still appears not to work, which is
why it is called out rather than left in a setup list.

## What we do with it

One call, by hand, from a script that exists for this and never runs in CI:

```
GOOGLE_CLOUD_PROJECT_ID=PROJECT_ID .venv/bin/python -m scripts.verify_route_optimization
```

Run it **as a module**. `python scripts/verify_route_optimization.py` fails with
`No module named 'app'`, because the repo root is only on `sys.path` under `-m`.

The scenario is built so the right answer is knowable in advance: two drivers ~15 km
apart in Austin, four orders clustered two-and-two beside each of them, deliberately
interleaved in the request so a solver that echoes input order produces a visibly absurd
plan.

| # | Check | Why it matters |
|---|---|---|
| 1 | Labels round-trip | If they don't, `service.py` cannot map an assignment back to an order and the dispatch cycle silently assigns nothing |
| 2 | No order was dropped | Skip penalties sit far above route cost, so a skip means the request is malformed |
| 3 | Each driver gets the cluster beside them | **The one that matters.** Catches vehicle costs missing or ignored, where every feasible plan scores identically and the returned sequence is arbitrary — the failure that looks most like success |
| 4 | Both legs came back per order | Confirms pickups and deliveries are both honoured, rather than the solver planning half the journey with optimistic travel times |
| 5 | The urgent order is collected first | Confirms collection deadlines are honoured (`L23`). A hot shot collected third looks like a perfectly normal route, so this cannot be eyeballed |

Check 5 is **the evidence that retires the HOT_SHOT hoist** in `accept_offer`, which
currently exists only because the solver is not yet trusted to prioritise the premium
tier.

An offline test asserts the scenario is satisfiable before anyone spends the paid call on
a broken assumption.

## Why it is worth the two minutes

**A wrong request here does not fail loudly — it returns a plausible plan.** Reading the
client against the documented API already turned up four real defects, including a
request with no objective function at all: vehicle costs default to zero and none were
set, so with skip penalties as the only cost every feasible plan scored identically. We
were paying for a solver and asking it to optimise nothing, while `considerRoadTraffic`
bought accurate traffic for a route nobody minimised.

Everything else is written and tested. This single call is the last unverified assumption
in the dispatch path, and three items are queued behind it:

- `G5` — per-job vehicle restriction (`allowedVehicleIndices`) for the gig demand path.
  Its row says *"do E1 first"*: extending an unverified integration means debugging two
  unknowns at once.
- `F6` — real-time mid-route re-optimisation.
- `L23` — removing the HOT_SHOT hoist, per check 5.

## If it fails

The message printed is Google's own, which is usually the actual fix — API not enabled,
credential missing `roles/cloudoptimization.user`, or billing off. A failed first attempt
still tells us precisely what to go back and ask for, so there is no downside to trying
before every box is confirmed ticked.

Add `--json` to dump the full request and response for eyeballing.
