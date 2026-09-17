"""The LMX palette and mark, in one place.

Two customer-facing PDFs now exist - the invoice (`app/billing/invoice_pdf.py`)
and the savings statement (`app/settle/pdf.py`) - which is the second live
caller CLAUDE.md's "no abstraction without two live callers" was waiting for.

**A drift worth naming rather than silently resolving.** `docs/DOCUMENT_STYLE.md`
and both frontends specify brand green `#0A6644`, the latter citing "the July
2026 brand decision" by name. `invoice_pdf.py` renders in navy `#1F3A5F`. So the
two documents a customer receives from us do not match each other, and one of
them does not match the brand.

The statement uses the documented green. The invoice is left alone: recolouring
a live billing artifact is a brand call rather than a refactor, and doing it
inside a PR about something else is how a decision gets made by accident. It is
flagged here so whoever makes that call can find both ends of it.

The mark lives under `app/billing/assets/` for historical reasons - it arrived
with the invoice. It is committed (CLAUDE.md permits product-embedded brand
assets, unlike the creative sources in `docs/LMX branding /`, which are
gitignored and would make any renderer depending on them fail in CI). Moving it
to a shared home is a one-line change worth making when a third caller appears.
"""
import os

# docs/DOCUMENT_STYLE.md, and --accent in dashboard/src/index.css.
GREEN = "#0A6644"
# The tint used for zebra striping and light fills - --accent-dim.
GREEN_TINT = "#E6F1EB"
# Body text that is not a heading. Not part of the brand decision; chosen to sit
# quietly under the green rather than compete with it.
SLATE = "#5B6472"

LOGO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "billing", "assets", "lmx-stamp.png"
)

# The source is 317x128, so anything else distorts it. ~99x40pt matches the
# invoice and lands inside the 0.28-0.32" the style guide asks for.
LOGO_WIDTH_PT = 99
LOGO_HEIGHT_PT = 40


def logo_is_available() -> bool:
    """Whether the mark can be drawn.

    Checked rather than assumed: a renderer that raised because a PNG was
    missing would fail the document, and a text header is a worse document
    rather than no document.
    """
    return os.path.exists(LOGO_PATH)
