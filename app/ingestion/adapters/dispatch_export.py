"""Read the incumbent dispatch platform's Customer Timing export.

`docs/ROADMAP_1.5.md` ING-4, and the file IDN-1..3 have to be run against
before any of them can be said to work. `lmx-dwell/` already parsed these for
the dwell baseline; this promotes that reading into `app/` so the same file can
seed identity.

**Not an order adapter.** Everything else in this package turns an inbound
message into a live order. This reads a historical report the customer's
outgoing vendor produces - it never creates work, it describes work already
done. It sits here because the file format is negotiated with somebody outside
this company, which is the test the architecture boundary actually applies.

**The shape, and why the parsing is defensive.** The file is a report, not a
data feed: several preamble lines (title, date range, timezone, a tenant slug),
then a header row, then rows. The header's position is not fixed, so it is
found rather than assumed - a report regenerated with one more line of preamble
must not silently shift every column by one.

**What the columns turn out to be**, which is worth stating because two of them
are not what their names suggest:

  `UDID`         the account id, `ROOT/BRANCH[-SUFFIX]`. 230 of them.
  `Destination`  the account NAME, despite the name. ~225 distinct.
  `Site Name`    the *sending* branch, not the customer. Two values.
  `City`, `Zip`  the only geography there is.

**There is no street address column.** The finest location this file supports
is name-plus-city-plus-postcode, which means it cannot place a geofence and
cannot be geocoded to a door. That is a limit of the historical export, not of
live operation - `DRV-1`'s fences come from order addresses, which arrive
through a real adapter. But it does mean identity loaded from this file starts
as one dock per account, and the collapsing is `IDN-2`'s job rather than
`IDN-1`'s.
"""
import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# A header row is the first row with more than this many non-empty cells. The
# preamble lines carry one or two; the header carries twenty-odd. Any threshold
# in between works, and being wrong is loud rather than silent - the columns
# simply will not be found.
_HEADER_MIN_CELLS = 5

_REQUIRED = ("UDID", "Destination", "City", "Zip/Postal Code")


@dataclass(frozen=True)
class ExportedStop:
    """One delivery as the outgoing platform recorded it."""

    account_ref: str
    account_name: str
    city: str | None
    postcode: str | None
    arrived_at: datetime | None
    departed_at: datetime | None

    @property
    def identity_address(self) -> str:
        """The best address string this file supports.

        Name first, then city and postcode. It is not a street address and must
        not be mistaken for one - see the module docstring. It exists so each
        account resolves to its own dock, which IDN-2 then collapses.
        """
        return ", ".join(p for p in (self.account_name, self.city, self.postcode) if p)

    @property
    def dwell_seconds(self) -> float | None:
        """Only where both edges exist and move forward.

        The export is minute-resolution, so most of these compute to zero -
        which is the finding that justifies DRV-1, not a parsing bug.
        """
        if self.arrived_at is None or self.departed_at is None:
            return None
        seconds = (self.departed_at - self.arrived_at).total_seconds()
        return seconds if seconds >= 0 else None


def _find_header(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows):
        if sum(1 for cell in row if cell.strip()) > _HEADER_MIN_CELLS:
            return index
    raise ValueError("no header row found - is this the Customer Timing report?")


def _parse_timestamp(raw: str) -> datetime | None:
    """Accept the formats this report has been seen to emit, and nothing else.

    A silently unparsed timestamp becomes a null dwell, which looks exactly
    like a stop that was never completed. Guessing with a loose parser would
    make that failure invisible, so the accepted formats are listed.
    """
    value = (raw or "").strip()
    if not value:
        return None
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def parse_customer_timing(path: str | Path) -> list[ExportedStop]:
    """Every stop row in the report, with the preamble skipped.

    Rows missing an account id are dropped: without one there is nothing to
    attach the stop to, and inventing an account would put a fabricated dock
    into the reference table IDN-1 exists to make trustworthy.
    """
    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        rows = list(csv.reader(handle))

    header_index = _find_header(rows)
    header = [cell.strip() for cell in rows[header_index]]
    columns = {name: index for index, name in enumerate(header) if name}

    missing = [name for name in _REQUIRED if name not in columns]
    if missing:
        raise ValueError(
            f"export is missing required column(s) {missing}; found {sorted(columns)}"
        )

    def cell(row: list[str], name: str) -> str:
        index = columns.get(name)
        if index is None or index >= len(row):
            return ""
        return row[index].strip()

    stops: list[ExportedStop] = []
    for row in rows[header_index + 1 :]:
        if not any(c.strip() for c in row):
            continue
        account_ref = cell(row, "UDID")
        if not account_ref:
            continue
        stops.append(
            ExportedStop(
                account_ref=account_ref,
                account_name=cell(row, "Destination"),
                city=cell(row, "City") or None,
                postcode=cell(row, "Zip/Postal Code") or None,
                arrived_at=_parse_timestamp(cell(row, "Arrived")),
                departed_at=_parse_timestamp(cell(row, "Departed")),
            )
        )
    return stops


def accounts_in(stops: list[ExportedStop]) -> dict[str, ExportedStop]:
    """One representative row per account, first occurrence wins.

    First rather than last so a re-run over an extended report produces the
    same representative for an account it already saw - the same reason the
    0046 backfill takes the first spelling of a dock.
    """
    accounts: dict[str, ExportedStop] = {}
    for stop in stops:
        accounts.setdefault(stop.account_ref, stop)
    return accounts
