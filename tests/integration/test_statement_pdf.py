"""The savings statement as a document (`STL-1`).

A PDF resists assertion, so these check the things that would actually harm
somebody: that the page never presents a midpoint the prose then retracts, that
it carries no name by default, and that a missing brand asset degrades the
document rather than failing it.

The layout itself was checked by rendering it and looking at the page, which is
what `docs/DOCUMENT_STYLE.md` asks for and what found the defect the first test
below now pins.
"""
import io
import os
import uuid
from datetime import datetime, timezone

import pytest
from pypdf import PdfReader

from app.brand import GREEN, LOGO_PATH
from app.settle.pdf import render_statement_pdf
from app.settle.statement import ArmComparison, ArmSample, SavingsStatement

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _comparison(difference: float, half_width: float) -> ArmComparison:
    return ArmComparison(
        control=ArmSample(arm="control", drops=40, mean_cents=1100.0, stdev_cents=400.0),
        treatment=ArmSample(arm="treatment", drops=360, mean_cents=1100.0 - difference,
                            stdev_cents=400.0),
        difference_cents=difference,
        half_width_cents=half_width,
    )


def _statement(comparison=None, unavailable=None, **kwargs) -> SavingsStatement:
    return SavingsStatement(
        client_id=uuid.uuid4(),
        period_start=START,
        period_end=END,
        drops=kwargs.pop("drops", 400),
        costed_drops=kwargs.pop("costed_drops", 400),
        cost_per_drop_cents=kwargs.pop("cost_per_drop_cents", 1000.0),
        comparison=comparison,
        comparison_unavailable=unavailable,
        caveats=kwargs.pop("caveats", ["This is a sample, not an audit."]),
        **kwargs,
    )


def _text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() for page in reader.pages)


class TestItNeverShowsAMidpointItThenRetracts:
    def test_a_range_spanning_zero_is_labelled_in_the_table(self):
        """The defect looking at the rendered page found. The first version put
        a bold "$1.13 per delivery" in the Difference row and explained
        underneath that the range included zero - and a reader skimming the
        table sees the bold number and stops."""
        pdf = render_statement_pdf(_statement(_comparison(113.0, 130.0)))
        text = _text(pdf)
        assert "not yet distinguishable from zero" in text

    def test_the_difference_row_is_a_range_even_when_it_is_a_clear_saving(self):
        """Same treatment either way. A row that switched from a range to a
        point estimate when the news was good would be a row that told you the
        news before you read it."""
        pdf = render_statement_pdf(_statement(_comparison(337.0, 130.0)))
        text = _text(pdf)
        assert "$2.07 to $4.67 per delivery" in text
        assert "not yet distinguishable" not in text

    def test_the_headline_survives_into_the_page(self):
        statement = _statement(_comparison(337.0, 130.0))
        text = _text(render_statement_pdf(statement))
        assert "does not yet show" not in text
        assert "less per delivery" in text


class TestItCarriesNoIdentity:
    def test_no_name_appears_by_default(self):
        """A statement circulates - forwarded, attached, screenshotted. One that
        names nobody says what LMX measured without saying whose account it was."""
        statement = _statement(_comparison(337.0, 130.0))
        text = _text(render_statement_pdf(statement))
        assert str(statement.client_id) not in text

    def test_a_caller_can_address_it_deliberately(self):
        statement = _statement(_comparison(337.0, 130.0))
        text = _text(render_statement_pdf(statement, addressed_to="Account 4021"))
        assert "Account 4021" in text


class TestItDegradesRatherThanFails:
    def test_a_missing_logo_still_produces_a_document(self, monkeypatch):
        """A text header is a worse document. A raised exception is no document,
        and the statement is the only thing in this system a customer sees."""
        import app.settle.pdf as module

        monkeypatch.setattr(module, "logo_is_available", lambda: False)
        pdf = render_statement_pdf(_statement(_comparison(337.0, 130.0)))
        assert pdf.startswith(b"%PDF")
        assert "LMX" in _text(pdf)

    def test_a_statement_with_no_comparison_still_renders(self):
        pdf = render_statement_pdf(
            _statement(None, unavailable="The comparison has not been switched on.")
        )
        assert "has not been switched on" in _text(pdf)

    def test_a_statement_with_nothing_costed_says_so(self):
        pdf = render_statement_pdf(
            _statement(None, unavailable="not on", cost_per_drop_cents=None, costed_drops=0)
        )
        assert "could be costed" in _text(pdf)


class TestTheDocumentItself:
    def test_it_is_one_page(self):
        pdf = render_statement_pdf(_statement(_comparison(337.0, 130.0)))
        assert len(PdfReader(io.BytesIO(pdf)).pages) == 1

    def test_it_is_titled_for_the_reader_not_the_filesystem(self):
        pdf = render_statement_pdf(_statement(_comparison(337.0, 130.0)))
        meta = PdfReader(io.BytesIO(pdf)).metadata
        assert meta.title == "Delivery savings statement"

    def test_it_uses_the_documented_brand_green(self):
        """docs/DOCUMENT_STYLE.md and both frontends, the latter citing the July
        2026 brand decision. app/brand.py records that the invoice does not."""
        assert GREEN == "#0A6644"

    def test_the_mark_is_committed_rather_than_on_the_shared_drive(self):
        """The creative sources in `docs/LMX branding /` are gitignored, so a
        renderer depending on them would fail in CI and for anybody without the
        drive."""
        assert os.path.exists(LOGO_PATH)
        assert "docs/LMX branding" not in LOGO_PATH
