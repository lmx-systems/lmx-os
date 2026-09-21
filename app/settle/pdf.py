"""The savings statement as a document somebody can be handed (`STL-1`).

`app/settle/statement.py` produces the text. This renders it, because a
statement that lives only in a terminal is not a statement - STL-1's done-when
is *"a customer can read it without a call"*, and a customer never opens a
terminal.

**The template must not look like good news.** This is the design constraint
that shaped everything below. The headline sentence is sometimes "we cannot show
a saving yet", and a layout that sets a big green number at the top reads as a
celebration whatever words are in it. So the headline sits in a neutral callout
with the accent used only as a left rule, the same weight and colour either way.
A template that looks triumphant regardless of what it says is a template that
misleads, and it would mislead in our favour, which is worse.

**No name on it by default.** CLAUDE.md's naming rule covers customer-facing
artifacts, and a statement circulates - it gets forwarded, attached, screenshotted.
The document identifies the period and nothing else unless a caller passes
`addressed_to` deliberately, so a copy that escapes says what LMX measured
without saying whose account it was.

**Brand green, and a mismatch flagged elsewhere.** See `app/brand.py`: the
invoice renders in navy, this renders in the documented green, and the two do
not match. Naming it there beat silently recolouring a live billing artifact
inside a PR about something else.
"""
from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as PdfImage,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.brand import (
    GREEN,
    GREEN_TINT,
    LOGO_HEIGHT_PT,
    LOGO_PATH,
    LOGO_WIDTH_PT,
    SLATE,
    logo_is_available,
)
from app.settle.statement import SavingsStatement, _money

_GREEN = colors.HexColor(GREEN)
_TINT = colors.HexColor(GREEN_TINT)
_SLATE = colors.HexColor(SLATE)


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "StatementTitle", parent=base["Title"], textColor=_GREEN,
            fontSize=19, alignment=0, spaceAfter=2,
        ),
        "period": ParagraphStyle(
            "StatementPeriod", parent=base["Normal"], textColor=_SLATE,
            fontSize=10, leading=14,
        ),
        # Not styled as a warning triangle, but not quiet either: the one thing
        # a reader must not do is mistake a draft for the final number.
        "draft": ParagraphStyle(
            "StatementDraft", parent=base["Normal"], textColor=colors.HexColor("#8A3A12"),
            fontSize=10, leading=14, backColor=colors.HexColor("#FDF1E7"),
            borderPadding=5, spaceBefore=2,
        ),
        # Deliberately not green and not large. The headline is sometimes the
        # disappointing sentence and must not be styled as a win.
        "headline": ParagraphStyle(
            "StatementHeadline", parent=base["Normal"], fontSize=13, leading=18,
        ),
        "heading": ParagraphStyle(
            "StatementHeading", parent=base["Heading2"], textColor=_GREEN,
            fontSize=11, leading=14, spaceBefore=14, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "StatementBody", parent=base["Normal"], fontSize=9.5, leading=14,
        ),
        "caveat": ParagraphStyle(
            "StatementCaveat", parent=base["Normal"], textColor=_SLATE,
            fontSize=9, leading=13, spaceAfter=5,
        ),
    }


def _headline_block(statement: SavingsStatement, styles: dict) -> Table:
    """The headline in a neutral box with an accent rule down the left.

    A `Table` rather than a coloured `Paragraph` so the emphasis comes from
    position and a rule rather than from colour - which is what lets the same
    treatment carry "we saved you money" and "we cannot show a saving" without
    the second one looking like a failure of the document.
    """
    block = Table(
        [[Paragraph(statement.headline, styles["headline"])]],
        colWidths=[7.0 * inch],
    )
    block.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _TINT),
                ("LINEBEFORE", (0, 0), (0, -1), 3, _GREEN),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return block


def _difference_cell(comparison) -> str:
    """The interval, phrased so it cannot be read as a point estimate."""
    span = f"{_money(comparison.low_cents)} to {_money(comparison.high_cents)}"
    if comparison.spans_zero:
        return f"{span} — not yet distinguishable from zero"
    return f"{span} per delivery"


def _comparison_table(statement: SavingsStatement, styles: dict):
    comparison = statement.comparison
    if comparison is None:
        return Paragraph(statement.comparison_unavailable or "", styles["body"])

    rows = [
        ["", "Deliveries", "Average cost each"],
        [
            "Dispatched the old way",
            str(comparison.control.drops),
            _money(comparison.control.mean_cents),
        ],
        [
            "Dispatched the new way",
            str(comparison.treatment.drops),
            _money(comparison.treatment.mean_cents),
        ],
        # The range, never the midpoint - and this row is why.
        #
        # Rendered, the first version put a bold "$1.13 per delivery" here and
        # then a sentence underneath explaining that the range included zero. A
        # reader skimming the table sees the bold number and stops, which is the
        # exact failure `statement.py` refuses in prose and the layout
        # reintroduced. Only looking at the page showed it.
        [
            "Difference",
            "",
            _difference_cell(comparison),
        ],
    ]
    table = Table(rows, colWidths=[3.2 * inch, 1.6 * inch, 2.2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _GREEN),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, _TINT]),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, _GREEN),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def render_statement_pdf(
    statement: SavingsStatement,
    *,
    addressed_to: str | None = None,
    draft_reason: str | None = None,
) -> bytes:
    """One page a customer can read without a call.

    `addressed_to` is opt-in. See the module docstring: a statement circulates,
    and one that names nobody says what LMX measured without saying whose
    account it was.

    `draft_reason` marks the page as not final and says why, in the place a
    reader looks first. `STL-2` is the reason it exists: a statement resting on
    a basis nobody has signed, or on one it no longer reproduces under, must not
    be indistinguishable from one that is agreed. An escape hatch that produces
    an unmarked artifact is not an escape hatch, it is a bypass - which is why
    the caller supplies the reason rather than a boolean, and why the reason is
    printed rather than merely recorded.
    """
    styles = _styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title="Delivery savings statement",
        author="LMX",
    )

    elements: list = []
    if logo_is_available():
        logo = PdfImage(LOGO_PATH, width=LOGO_WIDTH_PT, height=LOGO_HEIGHT_PT)
        logo.hAlign = "LEFT"
        elements.extend([logo, Spacer(1, 0.14 * inch)])

    elements.append(
        Paragraph(
            "Delivery savings statement" if logo_is_available()
            else "LMX — Delivery savings statement",
            styles["title"],
        )
    )
    period = (
        f"{statement.period_start:%-d %B %Y} to {statement.period_end:%-d %B %Y}"
    )
    if addressed_to:
        period = f"{addressed_to} &nbsp;·&nbsp; {period}"
    elements.append(Paragraph(period, styles["period"]))
    if draft_reason:
        # Above the headline, not in a footer. A draft mark a reader meets after
        # the number has done its work is a disclaimer, not a warning.
        elements.append(Spacer(1, 0.12 * inch))
        elements.append(
            Paragraph(f"DRAFT — not final. {draft_reason}", styles["draft"])
        )
    elements.append(Spacer(1, 0.22 * inch))
    elements.append(_headline_block(statement, styles))

    elements.append(Paragraph("What we delivered", styles["heading"]))
    delivered = f"{statement.drops} deliveries were included in the measurement."
    if statement.cost_per_drop_cents is not None:
        how_many = (
            "all of them" if statement.costed_drops == statement.drops
            else f"{statement.costed_drops} of them"
        )
        delivered += (
            f" We were able to cost {how_many}, at an average of "
            f"{_money(statement.cost_per_drop_cents)} per delivery."
        )
    else:
        delivered += (
            " None of them could be costed: costing needs the driver's shift hours, "
            "and those are not recorded for this period."
        )
    elements.append(Paragraph(delivered, styles["body"]))

    elements.append(Paragraph("The comparison", styles["heading"]))
    elements.append(_comparison_table(statement, styles))
    if statement.comparison is not None:
        comparison = statement.comparison
        elements.append(Spacer(1, 0.10 * inch))
        elements.append(
            Paragraph(
                "We are 95% confident the true figure is between "
                f"{_money(comparison.low_cents)} and "
                f"{_money(comparison.high_cents)} per delivery.",
                styles["body"],
            )
        )
        if comparison.spans_zero:
            elements.append(
                Paragraph(
                    "That range includes zero, which means this period does not yet "
                    "show a saving. It does not mean there is none - only that we "
                    "cannot demonstrate one yet, and we would rather tell you that "
                    "than round it up.",
                    styles["body"],
                )
            )

    if statement.exclusions is not None:
        elements.append(
            KeepTogether(
                [
                    Paragraph("What this does not cover", styles["heading"]),
                    Paragraph(statement.exclusions.disclosure(), styles["body"]),
                ]
            )
        )

    caveats = [Paragraph("How to read this", styles["heading"])]
    caveats += [Paragraph(c, styles["caveat"]) for c in statement.caveats]
    elements.append(KeepTogether(caveats))

    document.build(elements)
    return buffer.getvalue()
