"""
Parsing a delivery manifest out of a CSV (docs/LMX_LINK_PLAN.md T3).

T3's exit criterion is *"a 40-row manifest imports, with bad rows reported and good
rows dispatched"*. L9's bulk paste already covers the pasting case with per-row
results; this is the file, which is what a distributor actually has - an export from
their counter system, emailed or dropped in.

**The parsing is the whole product here, not plumbing.** A manifest arrives with
whatever headers that system emits: `Ship To Address`, `Delivery Address`, `ADDRESS1`,
`Customer Address`. Demanding one exact spelling turns a five-second import into a
support conversation and a hand-edited spreadsheet, so the column matching is
deliberately generous. What it will NOT do is guess between two plausible columns -
see `_match_column`.

Two rules carried over from the paste path because they are the same rule:

  - **Never silently drop a row.** Every line comes back either as an order or as an
    error naming the line number, because a dispatcher who uploads 40 and gets 38
    deliveries with no explanation has lost two orders they still believe are coming.
  - **Not all-or-nothing.** One unreadable row must not discard the 39 that were fine.

This module is pure: text in, rows and errors out, no I/O. The endpoint feeds its
output through the same batch ingestion the paste path uses, so there is one way an
order gets created rather than two that drift.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

# A manifest is a day's worth of deliveries, not a data migration. The cap is here
# because a CSV upload is otherwise an unbounded read into memory, and because a file
# with 50,000 rows is a mistake worth catching rather than a job worth starting - every
# genuinely new address costs a geocoder call.
MAX_ROWS = 200

# Enough for MAX_ROWS of generous rows. Checked against the decoded text rather than
# trusting a Content-Length nobody has to send honestly.
MAX_BYTES = 256 * 1024

# Where csv.DictReader parks fields beyond the header count.
_OVERFLOW = "__overflow__"

# What each field can be called. Matched case- and punctuation-insensitively, longest
# first so `delivery address` wins over `address` on a file that has both.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "drop_address": (
        "delivery address",
        "deliver to address",
        "ship to address",
        "shipping address",
        "customer address",
        "drop address",
        "destination",
        "address",
    ),
    "reference": (
        "order number",
        "order ref",
        "order reference",
        "invoice number",
        "invoice",
        "reference",
        "ref",
        "po number",
        "po",
    ),
    "drop_contact_name": (
        "delivery contact",
        "contact name",
        "customer name",
        "ship to name",
        "deliver to",
        "customer",
        "contact",
        "name",
    ),
}


@dataclass(frozen=True)
class ParsedRow:
    # 1-based and counting the header, so it matches what the dispatcher sees in
    # their spreadsheet. An error saying "row 4" that means the fifth line is worse
    # than no line number.
    line_number: int
    drop_address: str
    reference: str | None
    drop_contact_name: str | None


@dataclass(frozen=True)
class RowError:
    line_number: int
    message: str


@dataclass(frozen=True)
class ParsedManifest:
    rows: list[ParsedRow]
    errors: list[RowError]
    # Which header we matched to which field, echoed back so a dispatcher can see we
    # read "Ship To Address" as the destination rather than wondering why every
    # delivery is going to the same place.
    column_mapping: dict[str, str]


class ManifestUnreadable(Exception):
    """The file as a whole can't be used - not a row problem.

    Distinct from a row error on purpose: a dispatcher whose address column is missing
    needs to fix the export, and reporting that as 40 identical row failures buries the
    one thing they have to do.
    """


def _normalize(header: str) -> str:
    return "".join(ch for ch in header.lower() if ch.isalnum() or ch.isspace()).strip()


def _match_column(headers: list[str], field: str) -> str | None:
    """The header that means `field`, or None.

    **Deliberately refuses to guess between equals.** Aliases are tried in order,
    most specific first, and the first alias that any header contains wins - so a file
    with both `Ship To Address` and `Bill To Address` maps the shipping one because
    that alias is listed and `bill to address` is not. A file with two headers matching
    the SAME alias is ambiguous, and mapping one arbitrarily would send every delivery
    to the wrong column silently; that raises instead.
    """
    normalized = {header: _normalize(header) for header in headers}
    for alias in _COLUMN_ALIASES[field]:
        matches = [header for header, norm in normalized.items() if norm == alias]
        if len(matches) > 1:
            raise ManifestUnreadable(
                f"Two columns look like the {field.replace('_', ' ')}: "
                f"{', '.join(matches)}. Rename one and re-export."
            )
        if matches:
            return matches[0]
    # Second pass: substring, for headers like "Delivery Address (full)".
    for alias in _COLUMN_ALIASES[field]:
        matches = [header for header, norm in normalized.items() if alias in norm]
        if len(matches) == 1:
            return matches[0]
    return None


def parse_manifest(text: str) -> ParsedManifest:
    """Turn a CSV into rows to ingest and errors to report."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ManifestUnreadable(
            f"That file is too large - manifests are limited to {MAX_BYTES // 1024}KB "
            f"and {MAX_ROWS} rows"
        )
    if not text.strip():
        raise ManifestUnreadable("That file is empty")

    try:
        # Sniffing handles the comma-vs-semicolon split that European exports produce,
        # and tab-separated files saved with a .csv extension.
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    # restkey: a row with more fields than headers parks the extras here as a list.
    # That is not an exotic case - it is what "900 Congress Ave, Austin TX" does in an
    # unquoted single-column file, which is exactly the file a counter system exports.
    reader = csv.DictReader(io.StringIO(text), dialect=dialect, restkey=_OVERFLOW)
    headers = [h for h in (reader.fieldnames or []) if h and h.strip()]
    if not headers:
        raise ManifestUnreadable("We couldn't read a header row from that file")

    address_column = _match_column(headers, "drop_address")
    if address_column is None:
        raise ManifestUnreadable(
            "We couldn't find a delivery address column. Name it "
            f"'Delivery Address' - we also recognise: {', '.join(_COLUMN_ALIASES['drop_address'][:4])}."
        )
    reference_column = _match_column(headers, "reference")
    contact_column = _match_column(headers, "drop_contact_name")

    mapping = {"drop_address": address_column}
    if reference_column:
        mapping["reference"] = reference_column
    if contact_column:
        mapping["drop_contact_name"] = contact_column

    rows: list[ParsedRow] = []
    errors: list[RowError] = []
    for index, raw in enumerate(reader):
        # +2: one for the header, one because spreadsheets count from 1.
        line_number = index + 2

        if len(rows) + len(errors) >= MAX_ROWS:
            errors.append(
                RowError(
                    line_number=line_number,
                    message=(
                        f"Not read - manifests are limited to {MAX_ROWS} rows. "
                        "Split the file and upload the rest."
                    ),
                )
            )
            # Reported rather than truncated silently, but only once: a 5,000-row file
            # shouldn't produce 4,800 identical errors.
            break

        _rejoin_overflow(raw, headers, dialect)

        address = (raw.get(address_column) or "").strip()
        if not address:
            if not any(_as_text(value).strip() for value in raw.values()):
                continue  # a blank line, which every export produces at the end
            errors.append(RowError(line_number=line_number, message="No delivery address"))
            continue
        if len(address) > 255:
            errors.append(
                RowError(line_number=line_number, message="Delivery address is too long")
            )
            continue

        rows.append(
            ParsedRow(
                line_number=line_number,
                drop_address=address,
                reference=_cell(raw, reference_column, 120),
                drop_contact_name=_cell(raw, contact_column, 120),
            )
        )

    if not rows and not errors:
        raise ManifestUnreadable("That file has a header but no rows")

    return ParsedManifest(rows=rows, errors=errors, column_mapping=mapping)


def _as_text(value) -> str:
    """One cell as text, tolerating the overflow list."""
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return str(value) if value is not None else ""


def _rejoin_overflow(raw: dict, headers: list[str], dialect) -> None:
    """Put split-by-accident fields back onto the last column.

    An unquoted address containing a comma arrives as several fields, and the extras
    land under `_OVERFLOW`. They came from the END of the row, so appending them to the
    last header reconstructs the original text - which for the common
    "one address column" export is exactly right.

    It cannot be right in every layout, and it is not trying to be: the alternative is
    dropping the tail of an address silently, which puts a driver somewhere wrong.
    """
    overflow = raw.pop(_OVERFLOW, None)
    if not overflow or not headers:
        return
    separator = getattr(dialect, "delimiter", ",")
    last = headers[-1]
    parts = [_as_text(raw.get(last))] + [str(part) for part in overflow]

    # **Only rejoin real content.** A blank line in a one-column file arrives as two
    # empty fields, and joining them produces "," - which is not empty, so the row stops
    # looking blank and gets reported as a failure. Every export ends with a blank line,
    # so that turns a clean import into a report full of phantom errors.
    if not any(part.strip() for part in parts):
        raw[last] = ""
        return
    raw[last] = separator.join(parts)


def _cell(raw: dict, column: str | None, max_length: int) -> str | None:
    """One optional field, trimmed and length-capped.

    Truncated rather than rejected: an over-long reference is a nuisance, and failing
    a whole delivery over a field nothing routes on would be the wrong trade.
    """
    if column is None:
        return None
    value = _as_text(raw.get(column)).strip()
    return value[:max_length] or None
