"""
Binding somebody else's column headers to the fields the metrics need.

This module exists because of the order the work happened in: the baseline metrics
were specified (docs/ROADMAP.md W9) before anyone had seen a row of the exports they
would be computed from. Two exports, from systems we do not control, whose headers are
whatever a vendor chose - `Driver`, `DriverCode`, `driver_id`, `Emp #`.

The tempting shortcut is to guess the headers and write a parser against the guess.
That produces a tool that looks finished, runs without error on the real file, and
reports a drops-per-driver-hour figure computed from the wrong column. A baseline is
the number every later claim of improvement is measured against; a *wrong* baseline is
worse than no baseline, because nothing downstream can detect it.

So the schema is a runtime input, not a compile-time assumption:

  - `ACTIVITY_FIELDS` / `INVOICE_FIELDS` name what a metric needs. They do not change
    when a vendor renames a column.
  - `SYNONYMS` is a best-effort auto-detection table. It is a convenience, and it is
    allowed to fail.
  - `--map canonical=Header` overrides auto-detection for anything it missed.
  - An unresolved *required* field raises, listing the headers actually present.

The last point carries the design's whole load. `resolve()` never falls back to a
positional guess, never matches on "close enough", and never returns a map with a
required field missing. A mapping this tool is unsure about is a question for a human,
and the failure is loud at load time rather than silent in an aggregate three steps
later.

Matching is case-insensitive and ignores spaces, underscores and punctuation, so
`Stop Ref`, `stop_ref` and `STOP-REF` are one name. That much normalisation is safe:
it cannot bind two *different* concepts together, only two spellings of one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Every canonical field and what it is for.
#
# "Required" is deliberately a very short list. Almost everything here is optional
# because we do not know what these exports contain, and a tool that refuses to run
# without a miles column is useless against a file that has no miles column. The
# metrics layer reports what it could not compute instead - see `metrics.py`.

ACTIVITY_FIELDS: dict[str, str] = {
    "driver_id": "Who performed the work. Groups engaged hours and drops.",
    "completed_at": "When the drop completed. Counts drops and dates them.",
    "stop_ref": "Stop identifier, for reconciling against the invoice export.",
    "route_ref": "Run/route/manifest identifier. Without it, batch rate is not computable.",
    "promised_at": "The commitment the drop was measured against, for on-time rate.",
    "shift_start": "Shift clock-on, one source of engaged hours.",
    "shift_end": "Shift clock-off, the other half of the same source.",
    "hours": "Engaged hours stated directly, preferred over shift arithmetic.",
    "miles": "Distance attributed to the row.",
}

INVOICE_FIELDS: dict[str, str] = {
    "amount": "What was charged. The numerator of cost per drop.",
    "stop_ref": "Stop identifier, for reconciling against the activity export.",
    "customer_ref": "Billed party, for per-customer breakdown.",
    "invoiced_at": "Invoice or service date, for windowing.",
    "stop_count": "Stops covered by the row, when a line bills more than one.",
    "miles": "Distance billed on the row.",
}

ACTIVITY_REQUIRED: tuple[str, ...] = ("driver_id", "completed_at")
INVOICE_REQUIRED: tuple[str, ...] = ("amount",)

# Best-effort header spellings. Additive and harmless: a miss costs an explicit --map,
# never a wrong answer, because nothing here is consulted once a field is mapped.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "driver_id": (
        "driver", "driverid", "drivercode", "drivername", "employee", "employeeid",
        "empno", "courier", "drv", "emp",
    ),
    "completed_at": (
        "completed", "completedat", "completiontime", "deliveredat", "delivered",
        "actualarrival", "stoptime", "arrivetime",
    ),
    "stop_ref": (
        "stop", "stopid", "stopref", "stopnumber", "stopno", "ticket", "ticketno",
        "orderno", "ordernumber", "tkt",
    ),
    "route_ref": (
        "route", "routeid", "routeref", "run", "runid", "manifest", "manifestid",
        "trip", "tripid", "rte",
    ),
    "promised_at": (
        "promised", "promisedat", "duetime", "dueat", "committime", "slatime",
        "windowend",
    ),
    "shift_start": ("shiftstart", "clockin", "clockedin", "starttime", "logon"),
    "shift_end": ("shiftend", "clockout", "clockedout", "endtime", "logoff"),
    "hours": ("hours", "engagedhours", "workedhours", "laborhours", "totalhours", "duration"),
    # "odometer" is deliberately NOT here. An odometer reading is a cumulative vehicle
    # reading, not a distance travelled on that row, and summing readings produces a
    # number in the millions that would silently destroy miles-per-drop. A file whose
    # only distance column is an odometer needs differencing, not mapping.
    "miles": ("miles", "mileage", "distance", "totalmiles", "tripmiles"),
    "amount": (
        "amount", "charge", "charges", "total", "totalcharge", "invoiceamount",
        "price", "revenue", "extendedprice",
    ),
    "customer_ref": (
        "customer", "customerid", "customerref", "account", "accountno", "billto",
        "client",
    ),
    "invoiced_at": ("invoicedate", "invoicedat", "date", "servicedate", "billdate"),
    "stop_count": ("stopcount", "stops", "numstops", "quantity", "qty", "pieces"),
}


class ColumnMappingError(Exception):
    """A required field could not be bound to a header, or a --map named nonsense."""


def normalize(header: str) -> str:
    """`" Stop Ref# "` -> `"stopref"`. Spelling differences only, never meaning."""
    return re.sub(r"[^a-z0-9]", "", header.strip().lower())


@dataclass
class ColumnMap:
    """Canonical field -> the exact header string as it appears in the file."""

    mapping: dict[str, str] = field(default_factory=dict)
    headers: tuple[str, ...] = ()
    auto_detected: frozenset[str] = frozenset()

    def get(self, row: dict[str, str], field_name: str) -> str | None:
        """The raw cell for `field_name`, or None if unmapped or blank.

        Blank and unmapped collapse to the same answer on purpose: both mean "this row
        cannot contribute to that metric", and every caller treats them alike.
        """
        header = self.mapping.get(field_name)
        if header is None:
            return None
        value = row.get(header)
        if value is None:
            return None
        value = value.strip()
        return value or None

    def has(self, field_name: str) -> bool:
        return field_name in self.mapping

    def describe(self) -> list[str]:
        """Human-readable account of what bound to what.

        Printed by `--describe` so that first contact with a real export is a mapping
        someone can eyeball, rather than a number they have to trust.
        """
        lines = []
        for canonical, header in sorted(self.mapping.items()):
            how = "auto" if canonical in self.auto_detected else "--map"
            lines.append(f"  {canonical:<14} <- {header!r}  ({how})")
        return lines


def resolve(
    headers: list[str],
    known: dict[str, str],
    required: tuple[str, ...],
    overrides: dict[str, str] | None = None,
    label: str = "file",
) -> ColumnMap:
    """Bind canonical fields to real headers, or raise saying exactly what is missing.

    Overrides are applied first and are never second-guessed: if a human says
    `driver_id` is the `Emp #` column, auto-detection does not get a vote. An override
    naming a header that is not in the file is an error rather than a no-op, because
    the realistic cause is a typo, and a silently ignored typo puts us straight back to
    computing a metric from the wrong column.
    """
    overrides = overrides or {}
    by_normalized: dict[str, str] = {}
    for header in headers:
        by_normalized.setdefault(normalize(header), header)

    mapping: dict[str, str] = {}

    for canonical, requested in overrides.items():
        if canonical not in known:
            raise ColumnMappingError(
                f"--map {canonical}=... is not a field of the {label}. "
                f"Known fields: {', '.join(sorted(known))}"
            )
        header = by_normalized.get(normalize(requested))
        if header is None:
            raise ColumnMappingError(
                f"--map {canonical}={requested!r} names a column the {label} does not "
                f"have. Present: {', '.join(repr(h) for h in headers)}"
            )
        mapping[canonical] = header

    auto: set[str] = set()
    for canonical in known:
        if canonical in mapping:
            continue
        # Exact canonical spelling wins, then the synonym table, in listed order.
        for candidate in (canonical, *SYNONYMS.get(canonical, ())):
            header = by_normalized.get(normalize(candidate))
            if header is not None and header not in mapping.values():
                mapping[canonical] = header
                auto.add(canonical)
                break

    missing = [f for f in required if f not in mapping]
    if missing:
        raise ColumnMappingError(
            f"The {label} is missing required field(s) {missing}. No header matched, "
            f"and none was supplied with --map.\n"
            f"Columns present: {', '.join(repr(h) for h in headers)}\n"
            f"Bind them explicitly, e.g. --map {missing[0]}='<the right column>'"
        )

    return ColumnMap(mapping=mapping, headers=tuple(headers), auto_detected=frozenset(auto))
