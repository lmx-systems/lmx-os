# The LMX design system

The look of every LMX screen: the ops console, the client portal and its
tracking page, and the driver app. Chosen in October 2026 from eleven explored
directions ("G, refined"): a soft grey ground, white tiles with large radii,
ink for anything you press, one lime tile for the number that matters, and
peach for what needs a person.

**It is the default for all new UI.** A screen that needs something this page
does not cover extends it here first, in the same change.

## Principles

1. **Light only.** No dark theme and no large dark surfaces. Ink appears as
   buttons, pills and text, never as a background behind content.
2. **The ground separates; borders don't.** White tiles sit on a grey ground
   with no border and no shadow. Inside a tile, a group is a soft grey well
   (`--surface-2`), again without a border.
3. **One thing to press.** Each row or card has at most one ink button; the
   rest are outlined pills.
4. **The screen should be calm on a good day** (decision D-SEAT). Lead with
   what needs a person; put configuration a page away; an empty list says so
   in words ("Nothing needs you").
5. **Numbers are the headline.** Big, extra-bold, tabular figures; labels
   small and quiet above them.
6. **Colour carries meaning, never alone.** Every status colour comes with a
   word or an icon.

## Colour

One set of names for all three apps, defined in `dashboard/src/index.css`
and copied by name into each app as it moves over (see "Where it applies").

| Token | Value | Use |
|---|---|---|
| `--bg-page` | `#EEEFF1` | The ground behind every tile |
| `--surface` | `#FFFFFF` | Tiles, inputs, outlined pills |
| `--surface-2` | `#F6F7F8` | Wells and rows inside a tile |
| `--surface-3` | `#ECEDF0` | Tracks (progress bars), quiet chips |
| `--border` / `--border-strong` | `#E6E7EA` / `#D3D5DA` | Dividers inside a tile; outlines of secondary pills and inputs |
| `--text-primary` | `#15161A` | Ink: body text, headings, primary buttons |
| `--text-secondary` | `#4B4E57` | Supporting text |
| `--text-muted` | `#6B6E78` | Labels, timestamps (4.5:1 on white and on `--surface-2`) |
| `--accent` | `#15161A` | Primary button, selected nav pill, focus ring |
| `--lime` | `#CDEB6B` | The one headline tile per page; "done" marks; badge on a selected pill |
| `--lime-ink` | `#2E3A0C` | Secondary text on lime |
| `--accent-dim` | `#E6F2C0` | Steps already passed, selected-but-quiet states |
| `--needs` / `--needs-dim` | `#A8330A` / `#FFE4D4` | Something a rule may not decide: icon tiles, counts on nav pills |
| `--green` `--blue` `--amber` `--red` (+ `-dim`) | `#1F7A4D` `#2F5FD0` `#8A5300` `#B42318` | Status only: delivered/available, scheduled/counting down, waiting, failed/late |
| `--brand` | `#0A6644` | The LMX logo and generated documents (`docs/DOCUMENT_STYLE.md`); not used for UI controls |

Lime is a fill, never text on white. Ink on lime is the only text it carries.

## Type

**Plus Jakarta Sans** (`@fontsource-variable/plus-jakarta-sans` on the web;
the bundled font in the driver app), falling back to Helvetica.

| Role | Size / weight / tracking |
|---|---|
| Page title | 44px (30px on phones), 800, −0.045em, `text-wrap: balance` |
| Headline number | 80–88px, 800, −0.06em, tabular |
| Tile number | 30–32px, 800, −0.04em, tabular |
| Tile title | 16px, 700 |
| Row title | 15.5px, 700 |
| Body | 14–15px, 400–500 |
| Label / meta | 12–13px, 500–600 |

## Shape and space

| Token | Value | Use |
|---|---|---|
| `--radius-lg` | 28px | Tiles (`Card`) |
| — | 20–22px | Wells and rows inside a tile |
| `--radius` | 12px | Inputs, small boxes |
| `--radius-pill` | 999px | Every button, nav pill, chip and badge |

Tiles have 24px padding; the gap between tiles is 16px. Pages are at most
1440px wide with a 32px side gutter (16px on phones).

## Components

| Component | Where | What it is |
|---|---|---|
| Tile | `Card` | White, 28px radius, 16px bold title with muted meta beside it, no border |
| Nav | `TopBar` | Pages as a white pill group; the open page an ink pill; a count beside a label in peach (lime on the open page) |
| Page header | `App.tsx` `PageHeader` | Optional quiet kicker, a large title, one sentence of description to the right |
| Primary button | `ui/buttons.ts` `primaryButton` | Ink pill, white bold text |
| Secondary button | `ui/buttons.ts` `pillButton` | White pill, 1.5px inset outline, ink outline on hover |
| Chip | `ui/Chip` | Filter pill; selected is ink |
| Number tile | `ui/KpiCard` | Label, big number, one line of context |
| Headline tile | `HomeTiles` `TouchesTile` | The page's one lime tile |
| Decision row | `ExceptionsPanel` | 44px peach icon tile, bold title, one-line detail, the clock, one ink **Decide** button; opens in place to show what happened, the playbook's call and the actions |
| Steps | `PlaybookPanel` `Steps` | Three pills a rule climbs: Suggests → Acts unless stopped → Acts alone; current in ink, passed in lime, ahead outlined |
| Toast | `ui/Toast` | White pill at the bottom with a lime check |

## Where it applies

| App | State |
|---|---|
| Ops console (`dashboard/`) | **Done.** Home (Needs you, Going ahead, person touches, Rules, map, the day's numbers), then Orders, Drivers, Rules, Record, and for admins Clients and Settings |
| Client portal (`client-portal/`) and its public tracking page | In progress: same tokens and components; still on the old green theme until that change lands |
| Driver app (`driver-app/`) | In progress: same colours, type and radii in `src/theme/tokens.ts`; one primary action per screen, large touch targets for sunlight and gloves |

Update this table in the change that moves an app over.

Touch targets of at least 44px are a requirement for the portal's and the
driver app's phone screens; the console is a desk screen and uses smaller
pills inside dense tiles.
