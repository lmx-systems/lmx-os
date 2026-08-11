"""
The terms and the privacy policy as loaded from `app/legal/content/`.

These are offline tests - no database, no Redis. What they are protecting is the
thing that made the whole signup flow dishonest before: a version string that lived
somewhere other than the document it named.

The properties that matter here are not really about parsing. They are:

  - **A version cannot be declared in two places**, because there is only one place
    it is read from.
  - **A published document must be dated.** "Which version was in force when they
    signed up" is the question a dispute turns on, and it is unanswerable for a
    published document with no effective date.
  - **The documents move together.** The terms incorporate the privacy policy by
    reference, so a published terms pointing at a draft policy is a live document
    citing one that does not exist.
"""
import re
from dataclasses import replace
from datetime import date

import pytest

from app.legal import documents as legal
from app.legal.documents import LegalDocumentError, _load, _parse_front_matter


def test_both_documents_load_and_declare_their_own_kind():
    assert legal.TERMS.kind == "terms"
    assert legal.PRIVACY.kind == "privacy"
    assert legal.DOCUMENTS == {"terms": legal.TERMS, "privacy": legal.PRIVACY}


def test_versions_are_well_formed_and_bodies_are_not_empty():
    for doc in legal.DOCUMENTS.values():
        assert doc.version.startswith("v")
        assert doc.version[1:].isdigit()
        assert doc.body.strip()
        assert doc.title


def test_current_terms_version_comes_from_the_document():
    """The point of the module. Not a constant, not a request field."""
    assert legal.current_terms_version() == legal.TERMS.version


def test_shipped_documents_are_still_drafts():
    """A guard, not a preference.

    If this fails somebody has published the terms - which is the intended one-line
    change, and exactly the change that should not happen by accident in a refactor.
    Publishing means counsel has returned the document; update this test then.
    """
    assert legal.TERMS.status == "draft"
    assert legal.PRIVACY.status == "draft"
    assert legal.documents_are_published() is False


def test_portal_paths_are_where_the_signup_checkbox_links():
    assert legal.TERMS.portal_path == "/terms"
    assert legal.PRIVACY.portal_path == "/privacy"


def test_publication_needs_both_documents(monkeypatch):
    """Publishing the terms alone is not publishing.

    The terms say "set out in our privacy policy, which forms part of these terms".
    Live terms citing a draft policy is the failure this rules out.
    """
    published_terms = replace(legal.TERMS, status="published", effective=date(2026, 8, 11))
    monkeypatch.setattr(legal, "TERMS", published_terms)
    assert legal.documents_are_published() is False

    published_privacy = replace(legal.PRIVACY, status="published", effective=date(2026, 8, 11))
    monkeypatch.setattr(legal, "PRIVACY", published_privacy)
    assert legal.documents_are_published() is True


# ---------------------------------------------------------------------------
# Parsing. A malformed document must fail loudly at import rather than boot into
# a service that cannot say what terms it is operating under.
# ---------------------------------------------------------------------------


def _front_matter(**fields) -> str:
    body = fields.pop("body", "## 1. Something\n\nText.")
    lines = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return f"---\n{lines}\n---\n{body}\n"


def test_missing_front_matter_is_an_error():
    with pytest.raises(LegalDocumentError, match="no front matter"):
        _parse_front_matter("terms", "## 1. These terms\n\nNo header at all.\n")


def test_a_published_document_without_an_effective_date_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(legal, "CONTENT_DIR", tmp_path)
    (tmp_path / "terms.md").write_text(
        _front_matter(document="terms", version="v1", status="published", title="T", effective="")
    )
    with pytest.raises(LegalDocumentError, match="no effective date"):
        _load("terms")


def test_a_published_document_with_a_date_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(legal, "CONTENT_DIR", tmp_path)
    (tmp_path / "terms.md").write_text(
        _front_matter(
            document="terms",
            version="v2",
            status="published",
            title="T",
            effective="2026-09-01",
        )
    )
    doc = _load("terms")
    assert doc.version == "v2"
    assert doc.is_published
    assert doc.effective == date(2026, 9, 1)


@pytest.mark.parametrize(
    "fields, expected",
    [
        # A version that would compare unequal to itself after a copy-paste.
        (dict(document="terms", version="1.0", status="draft", title="T"), "not of the form v1"),
        (dict(document="terms", version="V1", status="draft", title="T"), "not of the form v1"),
        (dict(document="terms", version="v1 ", status="draft", title="T"), None),
        # A file that says it is the other document. Catches a bad copy-paste
        # between terms.md and privacy.md, which would otherwise serve the privacy
        # policy as the terms.
        (dict(document="privacy", version="v1", status="draft", title="T"), "front matter says"),
        (dict(document="terms", version="v1", status="live", title="T"), "not draft or published"),
        (dict(document="terms", version="v1", status="draft", title=""), "no title"),
        (dict(document="terms", version="v1", status="draft", title="T", body=""), "no body"),
    ],
)
def test_malformed_front_matter_is_refused(tmp_path, monkeypatch, fields, expected):
    monkeypatch.setattr(legal, "CONTENT_DIR", tmp_path)
    (tmp_path / "terms.md").write_text(_front_matter(**fields))
    if expected is None:
        # `v1 ` with a trailing space: the parser strips values, so this is a valid
        # v1 rather than a distinct version. Asserted because the alternative - two
        # versions that render identically and compare unequal - is the exact bug the
        # narrow pattern exists to prevent.
        assert _load("terms").version == "v1"
        return
    with pytest.raises(LegalDocumentError, match=expected):
        _load("terms")


def test_the_real_documents_have_no_engineering_notes_left_in_them():
    """These get shown to a client. The counsel-facing commentary lives in
    docs/LEGAL_BRIEF.md, and a stray roadmap reference or `(Engineering note: ...)`
    reaching a customer would be its own small disaster."""
    for doc in legal.DOCUMENTS.values():
        assert "Engineering note" not in doc.body
        assert "ROADMAP" not in doc.body
        assert "docs/" not in doc.body


def test_unresolved_placeholders_are_marked_the_same_way_everywhere():
    """Everything still waiting on counsel or the insurance position is a bracketed
    block of capitals, so one grep is a complete to-do list rather than a partial one.

    This is what makes the drafts safe to hand over: the holes are impossible to read
    past, and impossible to leave in by accident once they are filled - publishing
    with one still present is caught below.
    """
    placeholders = [
        marker
        for doc in legal.DOCUMENTS.values()
        for marker in re.findall(r"\[[A-Z][A-Z /—-]*[A-Z][^\]]*\]", doc.body)
    ]
    assert placeholders, "the drafts should still be declaring what they are waiting on"
    # Every one says what it is waiting on rather than just being blank.
    for marker in placeholders:
        assert "pending" in marker.lower(), marker


def test_a_document_cannot_be_published_with_a_hole_still_in_it():
    """The invariant the placeholders buy.

    Nothing enforces this at load time on purpose - a draft is *supposed* to have
    holes. What must never happen is publishing one, so the check lives here, where it
    fires the moment somebody flips `status: published` before filling the brackets.
    """
    for doc in legal.DOCUMENTS.values():
        if doc.is_published:
            assert not re.search(r"\[[A-Z][A-Z /—-]*[A-Z][^\]]*\]", doc.body), (
                f"{doc.kind} is published but still contains a PENDING placeholder"
            )
