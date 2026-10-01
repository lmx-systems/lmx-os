"""The divergence report's note about the cycle interval, rendered.

The note divides the measured interval between shadow cycles by two - the
average wait before a cycle sees an order - to say how much the dispatch lead
understates a continuously-running system. It used to find that metric by name
alone, so a metric of that name arriving in any other shape would have taken
the whole report down with an AttributeError. Nothing rendered it in a test.
"""
from datetime import datetime, timezone

from app.reporting.measurement import Measurement, Rate
from app.shadow.divergence import DivergenceReport, render

INTERVAL = "interval between shadow cycles"
LEAD = "dispatch lead over the operation"


def _report(*metrics) -> DivergenceReport:
    return DivergenceReport(
        hub_id="hub-1",
        since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        until=datetime(2026, 9, 2, tzinfo=timezone.utc),
        metrics=list(metrics),
    )


def _measured(name: str, median: float) -> Measurement:
    return Measurement(name=name, target="-", median=median, p90=median * 1.5, sample_size=10)


def test_the_note_quotes_half_the_cycle_interval():
    text = render(_report(_measured(INTERVAL, 300.0), _measured(LEAD, 120.0)))
    assert "understates LMX OS by roughly 150s" in text


def test_a_rate_under_the_interval_name_does_not_take_the_report_down():
    text = render(_report(Rate(name=INTERVAL, target="-", numerator=1, denominator=2), _measured(LEAD, 120.0)))
    assert "understates" not in text
    assert LEAD in text


def test_an_unmeasured_interval_leaves_the_note_out():
    unmeasured = Measurement(name=INTERVAL, target="-", not_measured="fewer than two cycles")
    text = render(_report(unmeasured, _measured(LEAD, 120.0)))
    assert "understates" not in text
