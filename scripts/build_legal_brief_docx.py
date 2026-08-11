"""
Build the legal brief as a .docx in house style (docs/DOCUMENT_STYLE.md).

This is the one generated document here whose whole purpose is to be sent outside the
company, so it gets the same treatment as the cofounder review rather than being pasted
into an email: a lawyer reading it should be able to see at a glance which parts are
decisions we owe them and which are constraints the software already imposes.

Content mirrors `docs/LEGAL_BRIEF.md`. The markdown stays the working copy - it is what
gets edited as decisions land - and this script is the presentation of it. They are
expected to move in the same commit.

Usage:
    .venv/bin/python scripts/build_legal_brief_docx.py
    .venv/bin/python scripts/build_legal_brief_docx.py --out ~/Desktop/legal-brief.docx

Output is deliberately NOT committed - `.gitignore` covers *.docx.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from docx import Document

from docx_house_style import (
    REPO,
    body,
    bullet,
    byline,
    heading,
    kicker,
    logo,
    quote,
    setup,
    sub,
    table,
    title,
)

# The drafts are rendered from the app's own loader rather than re-read as text, so a
# Word copy cannot claim a version or a status the served document disagrees with. This
# module is stdlib-only - importing it does not drag in settings or secrets.
sys.path.insert(0, str(REPO))
from app.legal.documents import DOCUMENTS, LegalDocument  # noqa: E402

# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

CODE_CHANGES = [
    [
        "The version was client-supplied",
        "`TERMS_VERSION = 'v1'` lived in the signup page and was sent to the server, "
        "which stored whatever arrived. The only evidence of what an applicant agreed "
        "to was written by the applicant's browser.",
        "The version is declared in the document's own front matter. One module reads "
        "it, and the endpoint writes the server's value.",
    ],
    [
        "Nothing checked it was current",
        "A form left open across a terms change would silently record assent to text "
        "the applicant never saw.",
        "A mismatch is refused and nothing is written. The applicant reloads and reads "
        "the new version.",
    ],
    [
        "Nothing checked a document existed",
        "The checkbox named two documents in plain text, with nowhere to go and read "
        "them.",
        "Both are served and linked. A draft renders with a banner saying it is a draft "
        "and applies to nobody.",
    ],
    [
        "Nothing was ever deleted",
        "No retention mechanism of any kind existed, so any period a policy stated "
        "would have been untrue from the day it was published.",
        "A scheduled sweep deletes driver location trails past the stated period. Four "
        "other categories are named below as not yet enforced.",
    ],
]

INSURANCE = [
    ["Cap per consignment", "Cargo cover per shipment"],
    ["Aggregate cap", "Annual aggregate"],
    ["Claim window", "How long a client has to notify us of loss or damage"],
    [
        "Declared value limit",
        "The value above which we will not carry without agreeing in writing",
    ],
]

RETENTION = [
    [
        "Driver location trail",
        "90 days",
        "Yes - pruned by a scheduled sweep. Needs the daily schedule set up",
    ],
    ["Recipient tracking links", "Dead ~24h after delivery", "Yes"],
    [
        "Delivery and billing records, including recipient name and address",
        "Account life + 7 years",
        "No mechanism, and none needed - nothing deletes them",
    ],
    [
        "Proof-of-delivery photos and signatures",
        "2 years",
        "**No.** Object storage - belongs in a bucket lifecycle rule, not an "
        "application loop. Outstanding",
    ],
    [
        "Driver licence and insurance images",
        "While engaged + 4 years",
        "**No.** Same - storage lifecycle. Outstanding",
    ],
    ["SMS and call records", "2 years", "**No.** No mechanism yet"],
    ["Declined applications", "12 months", "**No.** No mechanism yet"],
]

TERMS_CLAUSES = [
    [
        "2 - Requesting an account",
        "Signup creates a pending client that cannot order. We need the right to "
        "decline without giving a reason, and to withdraw approval.",
    ],
    [
        "3 - What LMX does",
        "LMX is the carrier, not a broker or a software vendor. A positioning "
        "decision; getting it wrong here undermines it everywhere else.",
    ],
    [
        "4 - Orders, collection and delivery",
        "The system commits to a collect-by time and shows an estimated delivery time. "
        "That distinction has to survive into the contract or the estimate becomes a "
        "promise. Also covers configurable proof of delivery.",
    ],
    [
        "5 - Prices and payment",
        "Rates are set per tier at approval; there is no self-serve pricing. Covers "
        "cash collected at the door on the client's behalf.",
    ],
    [
        "6 - Service levels and credits",
        "A missed collection commitment automatically credits the invoice. The system "
        "performs that remedy without being asked, so it has to be a contractual one.",
    ],
    [
        "7 - Your customers' information",
        "We hold recipient names, addresses, phones and notes on the client's behalf. "
        "Incorporates the privacy policy by reference.",
    ],
    ["8 - Operational data", "Decision 2 above. The training and aggregation rights."],
    ["9 - Liability", "Decision 1 above. Four numbers pending the insurance position."],
    [
        "10 - Suspension and ending",
        "The client record supports withdrawal of approval, so the contract should too.",
    ],
    [
        "11 - Changes",
        "Version and timestamp are recorded per client, so versioned re-acceptance is "
        "supportable. The re-acceptance flow is NOT built: a version bump today would "
        "close signup to new applicants until they accept, but would not prompt "
        "existing clients.",
    ],
]

PUBLISH_STEPS = [
    "Counsel returns both documents.",
    "Paste the final text into the two content files, keeping the front-matter block.",
    "Fill every bracketed PENDING placeholder. A test fails if a published document "
    "still contains one.",
    "Set the status to published and add an effective date to both. A published "
    "document with no effective date refuses to load - the date is what a dispute "
    "turns on.",
    "Bump the version if the text changed materially since anything was recorded "
    "against it.",
    "Update the test that asserts the shipped documents are still drafts. It exists to "
    "make sure the previous step is deliberate.",
    "Schedule the retention sweep daily. **Do this before publishing, not after** - the "
    "policy states a retention period from the moment it is in force.",
    "Leave the unpublished-terms override unset. Publishing is how the door opens; the "
    "override is a demo affordance that logs a warning on every signup it lets through.",
]


def build(out: Path) -> None:
    doc = Document()
    setup(doc)

    logo(doc)
    kicker(doc, "Legal brief · for counsel")
    title(doc, "The two documents behind the signup checkbox")
    byline(doc, "LMX  ·  11 August 2026  ·  Sourabh Miglani")

    quote(
        doc,
        "Written by engineering. Not legal advice, and not reviewed by a lawyer. This is "
        "the covering memo for two drafts, and its job is to make the turnaround short: "
        "every clause exists because something in the running system depends on it, and "
        "this document says what.",
    )
    body(
        doc,
        "Please feel free to restructure entirely. What matters is that the final "
        "documents answer the questions below, because the software already behaves as "
        "if they do.",
        lead=True,
    )

    heading(doc, "1", "What this is, and where the drafts live")
    body(
        doc,
        "Two drafts accompany this memo: **client terms** and a **privacy policy**. They "
        "are the served copies - our client portal renders them at `/terms` and "
        "`/privacy`, and the signup checkbox links to them - so a redraft goes into "
        "those two files and nowhere else. There is deliberately no second copy.",
    )
    body(
        doc,
        "**Both are marked as drafts, and that closes our front door.** New-customer "
        "signup returns an error while either document is a draft. This used to be a "
        "warning in a code comment; it is now a runtime guard, because a signup records "
        "which version of the terms was accepted, and a version of an unapproved "
        "document records assent to nothing - which is worse than no record, since it "
        "looks like one.",
    )
    body(
        doc,
        "Everywhere the drafts are still waiting on a decision, the text carries a "
        "bracketed block of capitals naming what it is waiting for. Those are the only "
        "holes, they are impossible to read past, and an automated check refuses to let "
        "a document be published with one still in it.",
    )

    heading(doc, "2", "What changed in our systems, and why it had to")
    body(
        doc,
        "Worth reading before the clauses, because it changes what the acceptance record "
        "is actually worth in a dispute. Four defects, all now fixed.",
    )
    table(doc, ["Defect", "What it was", "What it is now"], CODE_CHANGES)

    heading(doc, "3", "Decisions we need, in the order they block things")

    sub(doc, "1.  The insurance position - blocks the liability clause")
    body(
        doc,
        "The liability clause is drafted as a shape with four numbers missing, because "
        "they cannot be invented: they are whatever cover is actually in place.",
    )
    table(doc, ["Placeholder", "What it needs"], INSURANCE)
    body(
        doc,
        "**This is the longest lead time of anything here** - a broker conversation, not "
        "a drafting session. Nothing else on this list has an external dependency "
        "measured in weeks, so it should start first.",
    )

    sub(doc, "2.  Operational-data and training rights")
    body(
        doc,
        "Training rights, cross-customer aggregation and anonymisation terms belong in "
        "our first customer's contract **before the first delivery**, not in a later "
        "amendment. Renegotiating this with a live customer is a materially worse "
        "conversation than having it now.",
    )
    body(doc, "The draft claims, in plain terms:")
    for text in [
        "The operational record of *how a delivery was performed* is ours.",
        "We analyse and train models across all the work we carry, not only one "
        "client's.",
        "Anything beyond running a client's own account is aggregated and de-identified.",
        "We do not use a client's prices, customer lists or volumes to a competitor's "
        "benefit.",
    ]:
        bullet(doc, text)
    body(
        doc,
        "That last sentence is a commitment we have to be able to keep, and it is the "
        "one most worth challenging. We need confirmation that the first three are "
        "enforceable as written and that the carve-out is the right shape.",
    )

    sub(doc, "3.  Retention periods - the privacy policy states these as facts")
    body(
        doc,
        "Every period below is **proposed by us, not decided**. The third column is what "
        "actually happens today, which is the part that matters: a policy stating a "
        "period nothing enforces is a promise about a thing that never happens.",
    )
    table(doc, ["Data", "Proposed", "Enforced today?"], RETENTION)
    body(
        doc,
        "Two things follow. The location figure is the only one that must agree with a "
        "setting in our code, so that number and the sentence in the policy have to move "
        "together. And the four unenforced rows either get built before the policy "
        "states them, or the policy should be vaguer instead - please tell us which.",
    )
    body(
        doc,
        "**One interaction to catch now:** proof-of-delivery retention interacts with the "
        "claim window in decision 1. Deleting the photograph of a delivery while a client "
        "can still claim for it would be an own-goal, so whatever the claim window is, "
        "proof retention must be longer.",
    )

    sub(doc, "4.  Sub-processors, contact details, governing law")
    body(doc, "Smaller, but each is a hole in a document nobody can publish around:")
    for text in [
        "**The sub-processor list**, and where each one processes. The policy currently "
        "describes them by role - hosting, text messaging, mapping and routing, payroll, "
        "email, file storage - because two of the six are not contracted yet, so naming "
        "them would be premature. It has to name them before it goes live.",
        "**A privacy contact address and email.**",
        "**Governing law and venue** for the terms.",
        "**State-specific privacy rights and response deadlines.**",
    ]:
        bullet(doc, text)

    sub(doc, "5.  Whether we take payment - changes the pricing clause")
    body(
        doc,
        "Still an open commercial decision on our side. The terms currently describe what "
        "is true: we invoice, we collect cash at the door on the client's behalf, and we "
        "are not a party to their transaction with their customer. If we start taking "
        "card payments that clause is wrong and a payments processor joins the "
        "sub-processor list.",
    )

    doc.add_page_break()
    heading(doc, "4", "Clause map - what in the system depends on each one")
    sub(doc, "Client terms")
    table(doc, ["Clause", "What depends on it"], TERMS_CLAUSES)

    sub(doc, "Privacy policy")
    body(
        doc,
        "Structured by **whose data it is** rather than by data type, because the three "
        "groups reach us completely differently and have different rights: businesses we "
        "deliver for, people receiving a delivery, and drivers.",
    )
    body(
        doc,
        "A delivery recipient never agreed to anything with us - their details arrived "
        "because a distributor sent them - so the rights section says so, and routes "
        "their requests back through the sender where that is the honest answer. Please "
        "check that framing carefully; it is the one structural choice in the document "
        "rather than a wording choice.",
    )
    body(
        doc,
        "The data inventory was written from our database schema, not from memory. "
        "Everything the policy lists is a field that exists.",
    )

    heading(doc, "5", "To publish")
    body(
        doc,
        "For our side, once the documents come back. Included so it is clear how short "
        "the path is after the four decisions above.",
    )
    for i, text in enumerate(PUBLISH_STEPS, start=1):
        body(doc, f"{i}.  {text}", indent=0.2)
    body(
        doc,
        "Steps 2 to 6 are a single change on our side. Everything expensive is in the "
        "decisions, and the insurance position is the critical path.",
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print(f"wrote {out}")
    print(f"  {out.stat().st_size // 1024} KB")


_SECTION = re.compile(r"^## (.+)$")


def _render_markdown(doc: Document, source: str) -> None:
    """The draft's own markdown, in house style.

    Same subset the portal's reader handles - `## N. Heading`, paragraphs, `- ` bullets,
    `**bold**` - because it is the same two files. Anything richer would be a formatting
    capability the documents do not use.
    """
    for raw in source.split("\n\n"):
        block = raw.strip()
        if not block:
            continue

        if block.startswith("- "):
            # One block holds a whole list; a wrapped bullet continues its own line
            # rather than starting a new item.
            items: list[str] = []
            for line in block.split("\n"):
                stripped = line.lstrip()
                if stripped.startswith("- "):
                    items.append(stripped[2:])
                elif items:
                    items[-1] += " " + stripped
            for item in items:
                bullet(doc, item)
            continue

        match = _SECTION.match(block)
        if match:
            # "## 4. Orders, collection and delivery" -> number in the brand green,
            # title beside it, which is what `heading` already does.
            text = match.group(1)
            number, _, rest = text.partition(". ")
            if rest and number.rstrip(".").isdigit():
                heading(doc, number.rstrip("."), rest)
            else:
                heading(doc, "", text)
            continue

        body(doc, block.replace("\n", " "))


def build_draft(document: LegalDocument, out: Path) -> None:
    """One of the two drafts, as its own Word file for redlining."""
    doc = Document()
    setup(doc)

    logo(doc)
    kicker(doc, f"Draft for review · version {document.version}")
    title(doc, document.title)
    byline(
        doc,
        f"Version {document.version}"
        + (f"  ·  in force from {document.effective}" if document.effective else "")
        + ("" if document.is_published else "  ·  NOT YET IN FORCE"),
    )

    if not document.is_published:
        # On the document itself, not only in the covering memo. A draft that travels
        # without this line is one forward from being treated as final.
        quote(
            doc,
            "This is a draft. It has not been finalised, it does not apply to anyone, and "
            "nobody has been asked to agree to it. Passages in [BRACKETED CAPITALS] are "
            "open items naming what they are waiting on.",
        )

    _render_markdown(doc, document.body)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print(f"wrote {out}")
    print(f"  {out.stat().st_size // 1024} KB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "docs" / "LMX_Legal_Brief.docx",
        help="where to write the brief (default: docs/, which is gitignored)",
    )
    parser.add_argument(
        "--brief-only",
        action="store_true",
        help="skip the two drafts. The default writes all three, because a covering memo "
        "with no attachments is not a thing anyone can act on.",
    )
    args = parser.parse_args()
    out = args.out.expanduser()
    build(out)

    if not args.brief_only:
        for kind, filename in (
            ("terms", "LMX_Client_Terms_DRAFT.docx"),
            ("privacy", "LMX_Privacy_Policy_DRAFT.docx"),
        ):
            build_draft(DOCUMENTS[kind], out.parent / filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
