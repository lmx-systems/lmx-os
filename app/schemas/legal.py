"""What the portal is told about the terms and the privacy policy.

The signup form does not decide which version it is presenting - it asks. That
makes `app/legal/documents.py` the only place a version is declared, and it means
the checkbox can link to the document instead of naming it in prose.
"""
from datetime import date

from pydantic import BaseModel


class LegalDocumentView(BaseModel):
    """One document, as a public reader sees it."""

    kind: str
    version: str
    title: str
    effective: date | None
    # Where to read it in the portal, e.g. "/terms". A path rather than an absolute
    # URL, because the portal may be served from more than one host and it knows its
    # own origin better than the API does.
    path: str
    published: bool


class LegalDocumentsView(BaseModel):
    """Both documents, plus the one flag the signup form acts on.

    `signup_open` is the API's answer to "can I show this form", so the portal never
    has to reimplement the both-documents-must-be-published rule. It is false while
    either document is a draft - unless the deliberate `allow_unpublished_terms`
    escape hatch is on, in which case the form works and the documents still
    honestly report themselves as unpublished.
    """

    terms: LegalDocumentView
    privacy: LegalDocumentView
    signup_open: bool


class LegalDocumentBody(BaseModel):
    """A document's full text, for the portal to render."""

    kind: str
    version: str
    title: str
    effective: date | None
    published: bool
    # Markdown. The portal renders it; the API does not decide how it looks.
    body: str
