# LMX OS — project instructions

Conventions and hard rules for anyone (human or Claude) working in this repo.
Kept deliberately short: this is the stuff that is expensive to rediscover or
costly to get wrong, not a summary of the plan. For the plan, read
`docs/ROADMAP.md`.

## Absolute rules

**Customer naming.** The real first-customer name, its abbreviation, and its
town must never appear in any investor-facing, data-room, or customer-facing
artifact — prose or diagrams. Use "Design Partner", "design partner", or
"Customer Warehouse". Individual field-research contacts may be named; the
company and location may not. This applies to code, tests, fixtures, and logs
too.

**LMX OS is never sold.** LMX is an operator priced per drop, not a software
vendor. LMX OS is built for LMX only and is never licensed or sold as SaaS to
other distributors. Any framing that implies a software tier, seat pricing, or
a product SKU is wrong.

**"LMX Link" is internal only.** It names the order-intake track in
engineering docs and the backlog. Customer-facing framing is "how you send us
orders" — never a named product. "LMX Lite" is retired and must not be reused:
"Lite" implies a Pro tier and invites a software-fees conversation that
contradicts the operator position.

## Before doing any analysis

**Check `origin/main` first.** `git fetch` and confirm you are not behind.
This repo moves fast — a stale local clone has previously caused a full
roadmap audit to report shipped work as unbuilt. Verify item status against
the code, not against memory or a PR title.

**Claude cannot push.** No SSH keys or credentials in the sandbox. Commit
locally, then hand over the exact push command. Never assume a commit reached
the remote.

## Where things live

| File | Role |
|---|---|
| `docs/ROADMAP.md` | The map — every open item, the decision log, the phased plan. Source of truth for status |
| `docs/NEXT_STEPS.md` | Row-by-row punch list, updated in place |
| `docs/ARCHITECTURE.md` | Technical handoff detail, stubs, and best-effort interpretations |
| `docs/LMX_LINK_PLAN.md` | The order-intake track: contract, design principles, sequence |
| `docs/DOCUMENT_STYLE.md` | House style for generated documents |
| `docs/ORDER_API.md`, `docs/WEBHOOKS.md` | External-facing interfaces |
| `app/legal/content/*.md` | The terms and privacy policy themselves — served, versioned, and the single copy |
| `docs/LEGAL_BRIEF.md` | Counsel's covering memo: what each clause depends on, the open decisions, how to publish |

Changing status in one of these means checking the others. All four have
drifted apart before.

## Roadmap item prefixes

`B` business/org · `E` core backend · `S` security · `D` ops dashboard ·
`A` driver app · `C` client-facing components · `P` autonomy partners ·
`F` competitive gaps · `I` intelligence layer · `R` risk & compliance ·
`W` operational workflow gaps · `T` testing/process · `G` gig-platform
demand path · `L` LMX Link intake track

Struck-through (`~~L1~~`) means done. The decision logs at the top of
`ROADMAP.md` are the authority when an item still reads "open" inline.

## Architectural principle that governs intake

**One canonical order object. Many source adapters. Many status sinks. The
core never knows where an order came from.** If an adapter needs a change
inside the SLA engine, hold queue, or optimizer, *the contract is wrong* —
fix the contract, not the core.

**This is enforced, not just documented.**
`tests/test_architecture_boundaries.py` parses the tree and fails if the dispatch
engine (`batch_queue`, `optimizer`, `sla`, `fleet_state`, `delivery`,
`compliance`) imports any adapter, client-facing package, or demand path. A new package under
`app/` must be classified there too, so the boundary can't be dodged by adding a
directory. If it fails, widen the contract — don't add the import.

`sla_owner` is the field that lets the demand paths coexist:
- `LMX` — LMX made the promise. The SLA engine classifies urgency and owns
  the clock. (Web form, CSV, REST, Epicor/MAM.)
- `EXTERNAL` — someone else promised. The engine does not classify; it takes
  the given window as a hard constraint. (Aggregator relay, enterprise EDI.)

The batch-hold queue behaves identically either way — it holds against
whatever deadline is on the object.

**Status write-back gets equal weight to intake.** A carrier that accepts
orders and goes quiet is a favour, not a carrier. It is an exit criterion,
never a follow-up ticket.

## Generated documents

House style is Aptos, 0.5" margins, brand green `#0A6644`, LMX logo in the
header. Full detail in `docs/DOCUMENT_STYLE.md`. Render to PDF and actually
look at the pages before delivering.

## Git hygiene

Business documents and brand source creatives live in `docs/` for convenience
but are **not** version-controlled — the shared drive is their source of
truth. `.gitignore` covers `*.docx`, `*.pptx`, `*.pdf`, the branding folder,
and `.claude/`. Product-embedded brand assets (`dashboard/public/`,
`client-portal/public/`, `driver-app/assets/`, `app/billing/assets/`) *are*
committed.

Before staging, check whether someone already has work in the index — commit
with an explicit pathspec (`git commit -- docs/FILE.md`) rather than a bare
`git add` that would absorb unrelated staged changes.
