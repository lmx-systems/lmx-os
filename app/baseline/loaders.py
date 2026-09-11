"""
Turning two vendor CSV exports into typed rows, and counting everything that failed.

The parsing here is deliberately forgiving about *format* and deliberately strict about
*silence*. Those pull in opposite directions and the split matters:

**Forgiving about format.** These files come out of somebody else's system and are
often opened in Excel on the way. Money arrives as `$1,234.56`, as `1234.56`, and as
`(45.00)` meaning negative. Dates arrive in at least five spellings. Refusing a row
because a vendor writes a dollar sign would throw away the baseline to make a point.

**Strict about silence.** Every row that cannot be parsed is *counted and attributed*,
never skipped. `LoadResult.issues` keeps the row number and the reason, and
`field_coverage` records how many rows yielded a usable value for each field. This is
what makes the tenth W9 metric - data completeness - a real output rather than a
rhetorical one, and it is the difference between "drops per driver-hour was 3.1" and
"drops per driver-hour was 3.1, computed from 62% of rows because the rest had no
parseable shift times".

An aggregate over a silently-filtered subset is the failure mode this module is built
to prevent. A baseline computed from the two-thirds of rows that happened to parse,
presented as if it came from all of them, is exactly the kind of number that survives
review and then quietly misprices a contract.

Money is parsed to `float`. These are averages over thousands of rows for comparison
against a published per-job figure, not ledger postings, and no value here is ever
written back to a billing surface.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.baseline.columns import (
    ACTIVITY_FIELDS,
    ACTIVITY_REQUIRED,
    INVOICE_FIELDS,
    INVOICE_REQUIRED,
    ColumnMap,
    resolve,
)

# Tried in order against a trimmed cell. ISO is attempted first, separately, because
# `fromisoformat` covers far more shapes than any format string.
TIMESTAMP_FORMATS: tuple[str, ...] = (
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%m/%d/%y %H:%M",
    "%m/%d/%y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%m-%d-%Y %H:%M",
    "%b %d %Y %H:%M",
    "%d-%b-%Y %H:%M",
    "%d-%b-%Y",
    "%Y/%m/%d %H:%M",
    "%H:%M:%S",
    "%H:%M",
)

_NUMBER_NOISE = re.compile(r"[$,\s]")


class BaselineLoadError(Exception):
    """The file could not be read at all - missing, empty, or headerless."""


def parse_number(raw: str | None) -> float | None:
    """`"$1,234.56"` -> 1234.56, `"(45.00)"` -> -45.0, anything unparseable -> None.

    Parentheses are accounting negation, which is how a credit or a reversal reaches a
    CSV from most billing systems. Reading `(45.00)` as positive 45 would flip the sign
    of every adjustment in the file and inflate the cost baseline.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = _NUMBER_NOISE.sub("", text)
    if text.endswith("-"):  # trailing-minus, a mainframe export convention
        text, negative = text[:-1], True
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def parse_timestamp(raw: str | None) -> datetime | None:
    """A vendor timestamp in any of the shapes we have seen, or None.

    A bare time-of-day (`"14:30"`) parses onto a placeholder date. Callers that need a
    real calendar date must not use it as one; it exists so a shift-end column holding
    only a clock time can still produce a duration.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def is_time_only(raw: str | None) -> bool:
    """True when the cell carried a clock time but no date."""
    if raw is None:
        return False
    text = raw.strip()
    if not text:
        return False
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


@dataclass
class RowIssue:
    line: int
    reason: str


@dataclass
class ActivityRow:
    line: int
    driver_id: str
    completed_at: datetime
    stop_ref: str | None = None
    route_ref: str | None = None
    promised_at: datetime | None = None
    shift_start: datetime | None = None
    shift_end: datetime | None = None
    hours: float | None = None
    miles: float | None = None

    @property
    def day(self) -> str:
        return self.completed_at.date().isoformat()


@dataclass
class InvoiceRow:
    line: int
    amount: float
    stop_ref: str | None = None
    customer_ref: str | None = None
    invoiced_at: datetime | None = None
    stop_count: float | None = None
    miles: float | None = None


@dataclass
class LoadResult:
    """Rows that parsed, plus a full account of the ones that did not."""

    rows: list = field(default_factory=list)
    issues: list[RowIssue] = field(default_factory=list)
    column_map: ColumnMap = field(default_factory=ColumnMap)
    total_data_rows: int = 0
    field_coverage: dict[str, int] = field(default_factory=dict)
    path: str = ""

    @property
    def usable(self) -> int:
        return len(self.rows)

    def coverage_of(self, field_name: str) -> float | None:
        """Fraction of *parsed* rows carrying a usable value for `field_name`.

        None when the column is not mapped at all - "absent" and "present but empty"
        are different facts about the export, and flattening them to 0% would hide
        which one we are looking at.
        """
        if not self.column_map.has(field_name):
            return None
        if not self.rows:
            return 0.0
        return self.field_coverage.get(field_name, 0) / len(self.rows)


def read_csv(path: str | Path) -> tuple[list[dict[str, str]], list[str]]:
    """Rows and headers, tolerating a UTF-8 BOM and CRLF line endings."""
    p = Path(path)
    if not p.exists():
        raise BaselineLoadError(f"No such file: {p}")
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise BaselineLoadError(f"{p} is empty.")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BaselineLoadError(f"{p} has no header row.")
    headers = [h for h in reader.fieldnames if h is not None]
    return list(reader), headers


def _bump(coverage: dict[str, int], name: str, value: object) -> None:
    if value is not None:
        coverage[name] = coverage.get(name, 0) + 1


def load_activity(
    path: str | Path, overrides: dict[str, str] | None = None
) -> LoadResult:
    """Parse the driver-activity export. One row is expected to be one drop."""
    raw_rows, headers = read_csv(path)
    column_map = resolve(
        headers, ACTIVITY_FIELDS, ACTIVITY_REQUIRED, overrides, label="activity export"
    )
    result = LoadResult(column_map=column_map, total_data_rows=len(raw_rows), path=str(path))

    for offset, raw in enumerate(raw_rows, start=2):  # line 1 is the header
        driver_id = column_map.get(raw, "driver_id")
        completed_raw = column_map.get(raw, "completed_at")
        completed_at = parse_timestamp(completed_raw)

        if driver_id is None:
            result.issues.append(RowIssue(offset, "no driver id"))
            continue
        if completed_at is None:
            reason = (
                "no completion timestamp"
                if completed_raw is None
                else f"unparseable completion timestamp {completed_raw!r}"
            )
            result.issues.append(RowIssue(offset, reason))
            continue

        shift_start = parse_timestamp(column_map.get(raw, "shift_start"))
        shift_end_raw = column_map.get(raw, "shift_end")
        shift_end = parse_timestamp(shift_end_raw)

        # A shift recorded as clock times only loses its date, so an overnight shift
        # comes back as a negative duration. Push the end over the midnight boundary
        # rather than discarding the row or reporting negative hours worked.
        if (
            shift_start is not None
            and shift_end is not None
            and shift_end < shift_start
            and is_time_only(shift_end_raw)
        ):
            shift_end += timedelta(days=1)

        row = ActivityRow(
            line=offset,
            driver_id=driver_id,
            completed_at=completed_at,
            stop_ref=column_map.get(raw, "stop_ref"),
            route_ref=column_map.get(raw, "route_ref"),
            promised_at=parse_timestamp(column_map.get(raw, "promised_at")),
            shift_start=shift_start,
            shift_end=shift_end,
            hours=parse_number(column_map.get(raw, "hours")),
            miles=parse_number(column_map.get(raw, "miles")),
        )
        result.rows.append(row)
        for name in ("stop_ref", "route_ref", "promised_at", "shift_start", "shift_end", "hours", "miles"):
            _bump(result.field_coverage, name, getattr(row, name))
        _bump(result.field_coverage, "driver_id", row.driver_id)
        _bump(result.field_coverage, "completed_at", row.completed_at)

    return result


def load_invoices(
    path: str | Path, overrides: dict[str, str] | None = None
) -> LoadResult:
    """Parse the customer stop-invoice export. One row is expected to be one charge."""
    raw_rows, headers = read_csv(path)
    column_map = resolve(
        headers, INVOICE_FIELDS, INVOICE_REQUIRED, overrides, label="invoice export"
    )
    result = LoadResult(column_map=column_map, total_data_rows=len(raw_rows), path=str(path))

    for offset, raw in enumerate(raw_rows, start=2):
        amount_raw = column_map.get(raw, "amount")
        amount = parse_number(amount_raw)
        if amount is None:
            reason = (
                "no charge amount"
                if amount_raw is None
                else f"unparseable amount {amount_raw!r}"
            )
            result.issues.append(RowIssue(offset, reason))
            continue

        row = InvoiceRow(
            line=offset,
            amount=amount,
            stop_ref=column_map.get(raw, "stop_ref"),
            customer_ref=column_map.get(raw, "customer_ref"),
            invoiced_at=parse_timestamp(column_map.get(raw, "invoiced_at")),
            stop_count=parse_number(column_map.get(raw, "stop_count")),
            miles=parse_number(column_map.get(raw, "miles")),
        )
        result.rows.append(row)
        for name in ("stop_ref", "customer_ref", "invoiced_at", "stop_count", "miles"):
            _bump(result.field_coverage, name, getattr(row, name))
        _bump(result.field_coverage, "amount", row.amount)

    return result
