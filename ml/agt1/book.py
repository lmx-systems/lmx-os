"""The account book the bake-off resolves: one row per customer account id.

`ml/real/export.py` deliberately discards `receiver_name` at load - the class is
derived and the name thrown away, so that a structure that cannot hold the name
cannot leak it. **This loader cannot do that**, because *"are these two the same
place"* is a question about names and towns and nothing else in the export
answers it.

So the discipline moves rather than relaxing: this takes a path, the files stay
in gitignored `lmx-dwell/`, nothing here embeds a row, and the tests run against
invented places in invented towns. The same boundary, drawn one level out.

## What the export actually carries

`receiver_id`, `receiver_name`, `city`, `zip` - **and no street address.** Two
accounts in one town with one name cannot be separated by a house number,
because there is no house number. That is the constraint every resolver here
works under, and it is the reason a 100% score is not available to anybody.

One account per `receiver_id`, with the modal spelling of its name, town and
postcode across that account's stops. Modal rather than first-seen: a name is
mistyped on one manifest out of two hundred and the typo should not become the
account's identity.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Account:
    """One customer account id, as the whole book spells it."""

    receiver_id: str
    name: str
    city: str
    zip_code: str
    # How many stops this account accounts for. Not evidence of identity - it is
    # what tells a reviewer which pairs are worth their attention, and what says
    # how much a missed merge costs. A pair worth 400 stops splitting in two is
    # a different mistake from a pair worth three.
    stops: int

    @property
    def address(self) -> str:
        """The nearest thing to an address the export has.

        Ordered town-then-postcode so `account_signals.zip_of`, which anchors to
        the end of the string, reads the postcode rather than a house number
        that is not there. An account with neither is an empty string, which is
        the honest input: those exist, and they are the hardest rows in the book.
        """
        return ", ".join(part for part in (self.city, self.zip_code) if part)


def _modal(counter: Counter) -> str:
    """The commonest spelling, ties broken alphabetically for a stable book.

    Determinism matters more than the tie-break being right: the bake-off is
    re-run, and a book that reshuffles between runs would move both resolvers'
    scores for reasons that have nothing to do with either resolver.
    """
    if not counter:
        return ""
    best = max(counter.values())
    return sorted(name for name, count in counter.items() if count == best)[0]


def load_book(path: str | Path) -> list[Account]:
    """Read the whole-book export into one row per account.

    `stops_timing` rather than `stops_detail`: identity wants every account the
    distributor has, and the detail file is one driver's 54 manifests. Dwell is
    unreliable in the timing file and this does not read dwell.
    """
    names: dict[str, Counter] = defaultdict(Counter)
    cities: dict[str, Counter] = defaultdict(Counter)
    zips: dict[str, Counter] = defaultdict(Counter)
    stops: Counter = Counter()

    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            receiver_id = (row.get("receiver_id") or "").strip()
            if not receiver_id:
                continue
            stops[receiver_id] += 1
            for column, target in (
                ("receiver_name", names),
                ("city", cities),
                ("zip", zips),
            ):
                value = (row.get(column) or "").strip()
                if value:
                    target[receiver_id][value] += 1

    return [
        Account(
            receiver_id=receiver_id,
            name=_modal(names[receiver_id]),
            city=_modal(cities[receiver_id]),
            zip_code=_modal(zips[receiver_id]),
            stops=count,
        )
        for receiver_id, count in sorted(stops.items())
    ]
