"""The terms and the privacy policy, loaded from the documents themselves.

**The document file is the source of truth, not a constant somewhere else.** Before
this module, `client-portal/src/components/SignupPage.tsx` held
`TERMS_VERSION = 'v1'` and sent it to the server, which recorded whatever arrived.
Two things were wrong with that:

  - The acceptance record was **client-supplied**. `clients.terms_accepted_version`
    is the only evidence of what an applicant agreed to, and any caller could put
    any string in it. Evidence a stranger can write is not evidence.
  - The version lived somewhere other than the document, so bumping one without the
    other was a one-line mistake with no symptom.

Now the version, the title, the effective date and — the point of the exercise —
whether the document may be shown to anybody at all are declared in the front
matter of `content/terms.md` and `content/privacy.md`. Publishing is editing the
document, which is the only place it could honestly be decided.

**`status: draft` keeps the front door shut.** `app/api/public_routes.py` refuses
signups while either document is a draft, because a signup records assent to a
version, and a version of a document nobody has approved records assent to nothing.
That was previously a warning in three docstrings; it is now a 503. The escape hatch
is `settings.allow_unpublished_terms`, which exists for demos, defaults to off, and
logs every time it is used.

Both documents move together. The terms incorporate the privacy policy by
reference, so publishing the terms while the policy is still a draft would put a
live document in front of a client pointing at one that does not exist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

CONTENT_DIR = Path(__file__).parent / "content"

DocumentKind = Literal["terms", "privacy"]
DocumentStatus = Literal["draft", "published"]

# `v1`, `v2`, ... Deliberately narrow: the version is stored in a 32-char column,
# read back in an ops panel, and compared for equality on every signup. A free-form
# string invites `v1 `, `V1` and `1.0` to mean the same thing and be unequal.
_VERSION_PATTERN = re.compile(r"^v[0-9]+$")

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)

# Where each document is readable in the client portal. The checkbox on the signup
# page links here, so an applicant can read what they are accepting - which is the
# difference between a record of assent and a record of a tick.
_PORTAL_PATHS: dict[str, str] = {"terms": "/terms", "privacy": "/privacy"}


class LegalDocumentError(RuntimeError):
    """A document is malformed. Raised at import, on purpose.

    Failing to boot is the correct response to an unparseable legal document: the
    alternative is an application that starts and then cannot say what version of
    its terms it is operating under.
    """


@dataclass(frozen=True)
class LegalDocument:
    kind: DocumentKind
    version: str
    status: DocumentStatus
    title: str
    effective: date | None
    body: str

    @property
    def is_published(self) -> bool:
        return self.status == "published"

    @property
    def portal_path(self) -> str:
        return _PORTAL_PATHS[self.kind]


def _parse_front_matter(kind: str, raw: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER.match(raw)
    if match is None:
        raise LegalDocumentError(f"{kind}: no front matter block at the top of the file")
    fields: dict[str, str] = {}
    for line_number, line in enumerate(match.group(1).splitlines(), start=2):
        if not line.strip():
            continue
        if ":" not in line:
            raise LegalDocumentError(f"{kind}: line {line_number} is not `key: value`")
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, match.group(2).strip()


def _load(kind: DocumentKind) -> LegalDocument:
    path = CONTENT_DIR / f"{kind}.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - a packaging failure, not a code path
        raise LegalDocumentError(f"{kind}: cannot read {path}") from exc

    fields, body = _parse_front_matter(kind, raw)

    declared = fields.get("document")
    if declared != kind:
        raise LegalDocumentError(f"{kind}: front matter says document: {declared!r}")

    version = fields.get("version", "")
    if not _VERSION_PATTERN.match(version):
        raise LegalDocumentError(f"{kind}: version {version!r} is not of the form v1")

    status = fields.get("status", "")
    if status not in ("draft", "published"):
        raise LegalDocumentError(f"{kind}: status {status!r} is not draft or published")

    title = fields.get("title", "").strip()
    if not title:
        raise LegalDocumentError(f"{kind}: no title")

    raw_effective = fields.get("effective", "").strip()
    effective = date.fromisoformat(raw_effective) if raw_effective else None

    # You cannot publish a document without dating it. The date is what a dispute
    # turns on ("which version was in force when they signed up"), and a published
    # document with no date makes that unanswerable.
    if status == "published" and effective is None:
        raise LegalDocumentError(f"{kind}: published but has no effective date")

    if not body:
        raise LegalDocumentError(f"{kind}: no body")

    return LegalDocument(
        kind=kind,
        version=version,
        status=status,  # type: ignore[arg-type]
        title=title,
        effective=effective,
        body=body,
    )


TERMS = _load("terms")
PRIVACY = _load("privacy")

DOCUMENTS: dict[str, LegalDocument] = {"terms": TERMS, "privacy": PRIVACY}


def current_terms_version() -> str:
    """The version a signup is recorded against. Server-side, always."""
    return TERMS.version


def documents_are_published() -> bool:
    """Whether there is a real document behind the signup checkbox.

    Both, not either. The terms incorporate the privacy policy by reference.
    """
    return TERMS.is_published and PRIVACY.is_published


def acceptance_is_current(accepted_version: str | None) -> bool:
    """Whether a client who accepted `accepted_version` is still up to date.

    The gap this closes. `POST /public/signup` compares an applicant's version against
    the current one and refuses a stale submission, so publishing a new version closes
    the front door until people accept it. Nothing did the equivalent for clients who
    were **already through it** - so a version bump left existing clients placing orders
    under terms they had never seen, which is the one case the version column exists to
    make impossible.

    Clause 11 of the terms is the authority for gating rather than merely prompting:
    *"we may ask you to accept the new version before you place further orders."*

    Three answers, and the first is the one worth stating:

      - **Drafts oblige nobody.** While either document is unpublished there is nothing
        legitimate to accept, so this returns True and no client is prompted. Demanding
        assent to a draft is the same defect as recording it, which
        `documents_are_published` already refuses at signup.
      - A matching version is current.
      - Anything else is stale, **including `None`** - a client onboarded by ops through
        `POST /admin/clients` never accepted anything, and treating an absent record as
        satisfied would let exactly those clients order under no terms at all.
    """
    if not documents_are_published():
        return True
    return accepted_version == TERMS.version
