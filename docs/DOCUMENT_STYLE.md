# House style for generated documents

Standing defaults for every `.docx` and `.pptx` built for LMX from here on
(decision memos, slide decks, briefs, etc.) — so each one doesn't have to
be retrofitted with branding later, the way the payroll memo, brand asset
inventory doc, and GTM slide deck were in July 2026.

## Word documents (.docx)

- **Font:** Aptos, throughout (`styles.default.document.run` if building
  with `docx`-js).
- **Margins:** narrow — 0.5" all sides (720 DXA).
- **Accent color:** LMX brand green `#0A6644` for headings, table header
  fills, and borders. Table zebra-striping / light backgrounds use the
  tint `#E6F1EB` (same tokens as `--accent` / `--accent-dim` in the
  dashboard and client-portal CSS — one palette everywhere).
- **Logo:** `docs/LMX branding /lmx-logo-lockup-light.png` (or the
  transparent stamp, `lmx-logo-stamp-transparent.png`, for a smaller
  mark) placed at the top of the first page, ~0.28–0.32" tall. Replaces
  a plain "LMX" or "LMX OS" text label — don't leave the header text-only.

## Slide decks (.pptx)

- **Accent color:** same green (`#0A6644`) for titles and any accent
  shapes/icons — not the retired teal (`#0C8599`).
- **Logo:** the same lockup image, ~0.3–0.35" tall, top-right corner of
  every slide, small margin from the top/right edges.
- **Icons:** if icons are rendered as raster PNGs (e.g. via react-icons +
  sharp) rather than vector shapes, recolor the pixels directly to the
  accent green rather than leaving them in the old color — a plain
  find/replace on the XML won't touch baked-in icon pixels.

## Source of truth

Creative source files live in `docs/LMX branding /` (kept out of git,
per `.gitignore` — the shared drive is the real source of truth). No
vector master (SVG/AI) exists yet; that remains the one open item that
would make all of the above simpler (see
`docs/LMX_Brand_Asset_Inventory.docx`, section 5).
