"""
The baseline tool, exercised against synthetic exports with known answers.

Most of these tests are about the tool *declining* to produce a number. That is the
emphasis on purpose: this thing computes the figure every later claim of improvement is
measured against, and the dangerous failure is not a crash - it is a plausible number
derived from the wrong column, or an average over the third of rows that happened to
parse, printed with no indication that anything was dropped.

Fixture data is deliberately anonymous (`D1`, `SHOP-1`). Per `CLAUDE.md` the design
partner's name must not appear in code, tests or fixtures.
"""
from __future__ import annotations

import json

import pytest

from app.baseline.columns import (
    ACTIVITY_FIELDS,
    ACTIVITY_REQUIRED,
    ColumnMappingError,
    resolve,
)
from app.baseline.loaders import (
    BaselineLoadError,
    load_activity,
    load_invoices,
    parse_number,
    parse_timestamp,
)
from app.baseline.metrics import HOURS_FROM_SPAN, compute, engaged_hours
from app.baseline.report import render_json, render_text

ACTIVITY_CSV = """Driver,Stop #,Route,Completed,Promised,Shift Start,Shift End,Miles
D1,SHOP-1,R1,2026-03-02 08:30,2026-03-02 09:00,2026-03-02 08:00,2026-03-02 12:00,5
D1,SHOP-2,R1,2026-03-02 09:30,2026-03-02 10:00,2026-03-02 08:00,2026-03-02 12:00,5
D1,SHOP-3,R1,2026-03-02 10:30,2026-03-02 11:00,2026-03-02 08:00,2026-03-02 12:00,5
D1,SHOP-4,R1,2026-03-02 11:30,2026-03-02 11:00,2026-03-02 08:00,2026-03-02 12:00,5
D2,SHOP-5,R2,2026-03-02 08:45,2026-03-02 09:00,2026-03-02 08:00,2026-03-02 10:00,5
D2,SHOP-6,R2,2026-03-02 09:45,2026-03-02 10:00,2026-03-02 08:00,2026-03-02 10:00,5
"""

INVOICE_CSV = """Stop #,Bill To,Invoice Date,Amount
SHOP-1,ACCT-1,2026-03-02,$25.00
SHOP-2,ACCT-1,2026-03-02,$25.00
SHOP-3,ACCT-1,2026-03-02,$25.00
SHOP-4,ACCT-2,2026-03-02,$25.00
SHOP-5,ACCT-2,2026-03-02,$25.00
SHOP-6,ACCT-2,2026-03-02,$25.00
"""


@pytest.fixture
def activity_file(tmp_path):
    p = tmp_path / "activity.csv"
    p.write_text(ACTIVITY_CSV, encoding="utf-8")
    return p


@pytest.fixture
def invoice_file(tmp_path):
    p = tmp_path / "invoices.csv"
    p.write_text(INVOICE_CSV, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Value parsing - the formats a vendor export actually arrives in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$1,234.56", 1234.56),
        ("1234.56", 1234.56),
        ("  42 ", 42.0),
        ("(45.00)", -45.0),   # accounting negation: a credit, not a charge
        ("45.00-", -45.0),    # trailing minus, mainframe convention
        ("", None),
        (None, None),
        ("n/a", None),
        ("--", None),
    ],
)
def test_money_parses_the_way_billing_systems_write_it(raw, expected):
    assert parse_number(raw) == expected


def test_parenthesised_amounts_are_negative_not_positive():
    """A reversal read as a charge inflates the cost baseline silently."""
    assert parse_number("(1,000.00)") == -1000.0


@pytest.mark.parametrize(
    "raw",
    [
        "2026-03-02 08:30",
        "2026-03-02T08:30:00",
        "03/02/2026 08:30",
        "3/2/26 08:30",
        "2026/03/02 08:30",
        "02-Mar-2026 08:30",
    ],
)
def test_timestamps_parse_across_vendor_spellings(raw):
    assert parse_timestamp(raw) is not None


def test_an_unparseable_timestamp_is_none_not_an_exception():
    assert parse_timestamp("sometime tuesday") is None


# ---------------------------------------------------------------------------
# Column mapping - the core defence against computing the right metric on the
# wrong column
# ---------------------------------------------------------------------------


def test_headers_auto_detect_through_synonyms():
    column_map = resolve(
        ["Driver", "Completed", "Miles"], ACTIVITY_FIELDS, ACTIVITY_REQUIRED, label="activity"
    )
    assert column_map.mapping["driver_id"] == "Driver"
    assert column_map.mapping["completed_at"] == "Completed"
    assert column_map.mapping["miles"] == "Miles"


def test_a_missing_required_field_raises_and_lists_the_real_headers():
    """The error has to be actionable: the fix is a --map, and it needs the header."""
    with pytest.raises(ColumnMappingError) as exc:
        resolve(["Employee Number", "When"], ACTIVITY_FIELDS, ACTIVITY_REQUIRED, label="activity")
    message = str(exc.value)
    assert "completed_at" in message
    assert "'Employee Number'" in message
    assert "--map" in message


def test_an_override_beats_auto_detection():
    column_map = resolve(
        ["Driver", "Emp #", "Completed"],
        ACTIVITY_FIELDS,
        ACTIVITY_REQUIRED,
        overrides={"driver_id": "Emp #"},
        label="activity",
    )
    assert column_map.mapping["driver_id"] == "Emp #"


def test_an_override_naming_a_missing_column_is_an_error_not_a_no_op():
    """A typo'd --map that silently did nothing is how you get a wrong baseline."""
    with pytest.raises(ColumnMappingError, match="does not"):
        resolve(
            ["Driver", "Completed"],
            ACTIVITY_FIELDS,
            ACTIVITY_REQUIRED,
            overrides={"driver_id": "Emp #"},
            label="activity",
        )


def test_an_odometer_column_does_not_bind_to_miles():
    """A cumulative reading is not a per-row distance.

    Summing odometer readings across a few thousand rows yields a number in the
    millions and turns miles-per-drop into noise - with no error, because the column
    parses perfectly as a float. Leaving it unbound forces the question instead.
    """
    column_map = resolve(
        ["Driver", "Completed", "Odometer"],
        ACTIVITY_FIELDS,
        ACTIVITY_REQUIRED,
        label="activity",
    )
    assert "miles" not in column_map.mapping


def test_mapping_ignores_case_spacing_and_punctuation_only():
    column_map = resolve(
        ["  DRIVER_ID ", "Completed-At"], ACTIVITY_FIELDS, ACTIVITY_REQUIRED, label="activity"
    )
    assert column_map.mapping["driver_id"] == "  DRIVER_ID "
    assert column_map.mapping["completed_at"] == "Completed-At"


# ---------------------------------------------------------------------------
# Loading - nothing is dropped quietly
# ---------------------------------------------------------------------------


def test_unusable_rows_are_counted_and_attributed(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text(
        "Driver,Completed\n"
        "D1,2026-03-02 08:30\n"
        ",2026-03-02 09:30\n"            # no driver
        "D2,not a date\n"                # unparseable stamp
        "D3,\n",                         # empty stamp
        encoding="utf-8",
    )
    result = load_activity(p)
    assert result.usable == 1
    assert result.total_data_rows == 4
    assert len(result.issues) == 3
    reasons = " ".join(i.reason for i in result.issues)
    assert "no driver id" in reasons
    assert "unparseable" in reasons
    # Line numbers point at the spreadsheet row, header included.
    assert {i.line for i in result.issues} == {3, 4, 5}


def test_a_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(BaselineLoadError, match="No such file"):
        load_activity(tmp_path / "nope.csv")


def test_an_empty_file_is_a_clear_error(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(BaselineLoadError, match="empty"):
        load_activity(p)


def test_a_utf8_bom_does_not_break_the_first_header(tmp_path):
    """Excel writes a BOM; without utf-8-sig the first column binds to nothing."""
    p = tmp_path / "bom.csv"
    p.write_text("﻿Driver,Completed\nD1,2026-03-02 08:30\n", encoding="utf-8")
    result = load_activity(p)
    assert result.usable == 1
    assert result.column_map.mapping["driver_id"] == "Driver"


def test_coverage_distinguishes_absent_from_empty(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text(
        "Driver,Completed,Miles\nD1,2026-03-02 08:30,5\nD2,2026-03-02 09:30,\n",
        encoding="utf-8",
    )
    result = load_activity(p)
    assert result.coverage_of("miles") == 0.5        # mapped, half populated
    assert result.coverage_of("route_ref") is None   # not in the file at all


# ---------------------------------------------------------------------------
# Engaged hours - the derivation with the most room to be quietly wrong
# ---------------------------------------------------------------------------


def test_engaged_hours_come_from_shift_times(activity_file):
    result = load_activity(activity_file)
    hours = engaged_hours(result.rows)
    assert hours is not None
    assert hours.total == pytest.approx(6.0)   # D1 4h + D2 2h
    assert hours.driver_days == 2
    assert hours.covered_days == 2


def test_a_daily_total_repeated_on_every_row_is_not_multiplied(tmp_path):
    """The trap: row-per-stop exports repeat the day's hours on each stop row.

    Summing them turns an 8-hour day into 32 and quarters drops-per-hour.
    """
    p = tmp_path / "a.csv"
    p.write_text(
        "Driver,Completed,Hours\n"
        "D1,2026-03-02 08:30,8\n"
        "D1,2026-03-02 09:30,8\n"
        "D1,2026-03-02 10:30,8\n"
        "D1,2026-03-02 11:30,8\n",
        encoding="utf-8",
    )
    hours = engaged_hours(load_activity(p).rows)
    assert hours is not None
    assert hours.total == pytest.approx(8.0)


def test_differing_per_row_hours_are_summed(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text(
        "Driver,Completed,Hours\nD1,2026-03-02 08:30,1.5\nD1,2026-03-02 09:30,2.5\n",
        encoding="utf-8",
    )
    hours = engaged_hours(load_activity(p).rows)
    assert hours is not None
    assert hours.total == pytest.approx(4.0)


def test_the_span_fallback_is_flagged_as_a_lower_bound(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text(
        "Driver,Completed\nD1,2026-03-02 09:00\nD1,2026-03-02 12:00\n", encoding="utf-8"
    )
    result = load_activity(p)
    hours = engaged_hours(result.rows)
    assert hours is not None
    assert hours.total == pytest.approx(3.0)
    assert hours.source == HOURS_FROM_SPAN

    baseline = compute(result, None)
    assert any("LOWER BOUND" in w or "OVERSTATEMENT" in w for w in baseline.warnings)


def test_an_overnight_shift_written_as_clock_times_is_not_negative(tmp_path):
    """`22:00` to `02:00` loses its date and comes back as -20 hours."""
    p = tmp_path / "a.csv"
    p.write_text(
        "Driver,Completed,Shift Start,Shift End\n"
        "D1,2026-03-02 23:30,22:00,02:00\n",
        encoding="utf-8",
    )
    hours = engaged_hours(load_activity(p).rows)
    assert hours is not None
    assert hours.total == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# The metrics themselves
# ---------------------------------------------------------------------------


def test_the_headline_rates_are_arithmetically_right(activity_file, invoice_file):
    baseline = compute(load_activity(activity_file), load_invoices(invoice_file))

    assert baseline.by_name("drops").value == 6
    assert baseline.by_name("engaged_hours").value == pytest.approx(6.0)
    assert baseline.by_name("drops_per_driver_hour").value == pytest.approx(1.0)
    assert baseline.by_name("miles_per_drop").value == pytest.approx(5.0)
    assert baseline.by_name("total_charged").value == pytest.approx(150.0)
    assert baseline.by_name("cost_per_billed_stop").value == pytest.approx(25.0)
    assert baseline.by_name("revenue_per_engaged_hour").value == pytest.approx(25.0)
    assert baseline.by_name("data_completeness").value == pytest.approx(100.0)


def test_on_time_rate_counts_only_drops_with_a_promise(activity_file):
    """Five of six met the promise; SHOP-4 completed 11:30 against 11:00."""
    baseline = compute(load_activity(activity_file), None)
    assert baseline.by_name("on_time_rate").value == pytest.approx(83.33, abs=0.01)


def test_batch_rate_and_drops_per_route(activity_file):
    baseline = compute(load_activity(activity_file), None)
    assert baseline.by_name("batch_rate").value == pytest.approx(100.0)
    assert baseline.by_name("drops_per_route").value == pytest.approx(3.0)
    # The granularity caveat must travel with the number.
    assert "granularity" in baseline.by_name("batch_rate").caveat


def test_every_metric_carries_its_basis(activity_file, invoice_file):
    baseline = compute(load_activity(activity_file), load_invoices(invoice_file))
    for metric in baseline.metrics:
        assert metric.basis, f"{metric.name} has no stated basis"


def test_a_metric_without_its_column_is_unavailable_not_zero(tmp_path):
    """Defaulting a missing metric to 0 reports perfect on-time for an operation
    that never recorded a promise time."""
    p = tmp_path / "a.csv"
    p.write_text("Driver,Completed\nD1,2026-03-02 08:30\n", encoding="utf-8")
    baseline = compute(load_activity(p), None)

    for name in ("on_time_rate", "miles_per_drop", "batch_rate"):
        metric = baseline.by_name(name)
        assert metric is not None
        assert metric.value is None, f"{name} should be unavailable, not a number"
        assert metric.basis, f"{name} must say why it is unavailable"


def test_miles_per_drop_uses_only_rows_that_have_miles(tmp_path):
    """Dividing by all drops makes a half-populated column look like shorter trips."""
    p = tmp_path / "a.csv"
    p.write_text(
        "Driver,Completed,Miles\n"
        "D1,2026-03-02 08:30,10\n"
        "D1,2026-03-02 09:30,10\n"
        "D1,2026-03-02 10:30,\n"
        "D1,2026-03-02 11:30,\n",
        encoding="utf-8",
    )
    baseline = compute(load_activity(p), None)
    assert baseline.by_name("miles_per_drop").value == pytest.approx(10.0)
    assert "only over rows with a miles value" in baseline.by_name("miles_per_drop").caveat


# ---------------------------------------------------------------------------
# Reconciliation between the two exports
# ---------------------------------------------------------------------------


def test_unbilled_and_unrecorded_stops_both_surface(tmp_path):
    activity = tmp_path / "a.csv"
    activity.write_text(
        "Driver,Stop #,Completed\n"
        "D1,SHOP-1,2026-03-02 08:30\n"
        "D1,SHOP-2,2026-03-02 09:30\n",
        encoding="utf-8",
    )
    invoices = tmp_path / "i.csv"
    invoices.write_text(
        "Stop #,Amount\nSHOP-1,25.00\nSHOP-9,25.00\n", encoding="utf-8"
    )
    baseline = compute(load_activity(activity), load_invoices(invoices))

    assert baseline.by_name("stop_ref_match_rate").value == pytest.approx(50.0)
    warnings = " ".join(baseline.warnings)
    assert "1 stop was completed but never billed" in warnings
    assert "1 stop was billed but does not appear" in warnings


def test_a_volume_disagreement_surfaces_even_without_a_shared_key(tmp_path):
    activity = tmp_path / "a.csv"
    activity.write_text(
        "Driver,Completed\n" + "".join(f"D1,2026-03-02 0{i}:30\n" for i in range(1, 10)),
        encoding="utf-8",
    )
    invoices = tmp_path / "i.csv"
    invoices.write_text("Amount\n25.00\n25.00\n", encoding="utf-8")
    baseline = compute(load_activity(activity), load_invoices(invoices))

    assert baseline.by_name("stop_ref_match_rate").value is None
    assert any("disagree on volume" in w for w in baseline.warnings)


def test_data_completeness_counts_both_files(tmp_path):
    activity = tmp_path / "a.csv"
    activity.write_text(
        "Driver,Completed\nD1,2026-03-02 08:30\nD2,garbage\n", encoding="utf-8"
    )
    invoices = tmp_path / "i.csv"
    invoices.write_text("Amount\n25.00\n25.00\n", encoding="utf-8")
    baseline = compute(load_activity(activity), load_invoices(invoices))

    assert baseline.by_name("data_completeness").value == pytest.approx(75.0)  # 3 of 4
    assert any("75.0% of rows were usable" in w for w in baseline.warnings)


# ---------------------------------------------------------------------------
# Rendering - the caveats must be as visible as the numbers
# ---------------------------------------------------------------------------


def test_the_text_report_shows_rejects_unavailable_metrics_and_warnings(tmp_path):
    activity = tmp_path / "a.csv"
    activity.write_text(
        "Driver,Completed\nD1,2026-03-02 09:00\nD1,2026-03-02 12:00\nD2,garbage\n",
        encoding="utf-8",
    )
    text = render_text(compute(load_activity(activity), None))

    assert "REJECTED" in text
    assert "NOT COMPUTABLE" in text
    assert "WARNINGS" in text
    assert "on_time_rate" in text
    # The metrics only a live system can produce are named, not silently absent.
    assert "re-plan speed" in text


def test_pilot_comparators_are_labelled_as_a_different_demand_path(
    activity_file, invoice_file
):
    text = render_text(compute(load_activity(activity_file), load_invoices(invoice_file)))
    assert "gig pilot" in text
    assert "not a like-for-like target" in text


def test_rendered_figures_keep_their_units(activity_file, invoice_file):
    """A bare column of numbers is ambiguous: $/stop and $/hr must be distinguishable."""
    text = render_text(compute(load_activity(activity_file), load_invoices(invoice_file)))
    assert "$25.00/stop" in text
    assert "$25.00/hr" in text
    assert "6 hr" in text          # engaged_hours keeps its unit
    assert "5 mi/drop" in text     # so does a plain rate


def test_json_output_is_valid_and_carries_the_column_map(activity_file, invoice_file):
    payload = json.loads(
        render_json(compute(load_activity(activity_file), load_invoices(invoice_file)))
    )
    assert payload["metrics"]["drops_per_driver_hour"]["value"] == pytest.approx(1.0)
    assert payload["sources"]["activity"]["column_map"]["driver_id"] == "Driver"
    assert payload["sources"]["invoices"]["usable_rows"] == 6


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


def _cli():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "analyze_baseline.py"
    spec = importlib.util.spec_from_file_location("analyze_baseline", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cli_end_to_end(activity_file, invoice_file, capsys):
    code = _cli().main(["--activity", str(activity_file), "--invoices", str(invoice_file)])
    out = capsys.readouterr().out
    assert code == 0
    assert "OPERATIONAL BASELINE" in out
    assert "drops_per_driver_hour" in out


def test_cli_describe_lists_headers_without_computing(activity_file, capsys):
    code = _cli().main(["--activity", str(activity_file), "--describe"])
    out = capsys.readouterr().out
    assert code == 0
    assert "'Shift Start'" in out
    assert "driver_id" in out
    assert "OPERATIONAL BASELINE" not in out


def test_cli_describe_still_prints_headers_when_mapping_fails(tmp_path, capsys):
    """--describe is what you reach for *because* the mapping failed."""
    p = tmp_path / "odd.csv"
    p.write_text("Weird One,Weird Two\na,b\n", encoding="utf-8")
    code = _cli().main(["--activity", str(p), "--describe"])
    out = capsys.readouterr().out
    assert code == 0
    assert "'Weird One'" in out


def test_cli_exits_nonzero_and_points_at_describe_when_a_column_is_missing(
    tmp_path, capsys
):
    p = tmp_path / "odd.csv"
    p.write_text("Weird One,Weird Two\na,b\n", encoding="utf-8")
    code = _cli().main(["--activity", str(p)])
    err = capsys.readouterr().err
    assert code == 2
    assert "--describe" in err


def test_cli_rejects_an_ambiguous_unprefixed_map(activity_file):
    """`stop_ref` exists in both exports, so an unprefixed --map must not guess."""
    with pytest.raises(SystemExit, match="ambiguous"):
        _cli().main(["--activity", str(activity_file), "--map", "stop_ref=Stop #"])


def test_cli_accepts_a_prefixed_map(activity_file, capsys):
    code = _cli().main(
        ["--activity", str(activity_file), "--map", "activity:stop_ref=Stop #", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["sources"]["activity"]["column_map"]["stop_ref"] == "Stop #"


def test_cli_needs_at_least_one_file():
    with pytest.raises(SystemExit):
        _cli().main([])
