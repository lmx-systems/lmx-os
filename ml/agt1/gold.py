"""The hand-labelled truth, and the two things the label file refuses to show.

One CSV, one column for a person to fill in, written once. Everything else in
this package is a statement about this file.

## It hides what the resolvers said

The obvious label file carries a `deterministic_says` column so the reviewer can
see what the incumbent thought. That column would destroy the measurement. A
reviewer shown a machine's answer agrees with it - not from laziness, from
anchoring - and the truth set would drift toward the incumbent until its
precision approached 100% by construction and the challenger was scored against
its opponent's opinion wearing a label's clothes.

So the file carries **evidence and no verdicts**: both names, both towns, both
postcodes, both account ids, both stop counts, and why the pair was pooled.
Nothing about what any resolver concluded.

## It hides the blocks' shape

Pairs are emitted in a **seeded shuffle** rather than grouped by reason. Grouped,
a reviewer meets forty `same account root` pairs in a row, answers yes forty
times, and is answering the block rather than the pair by the tenth. Shuffled,
every pair arrives on its own evidence. The seed is fixed so the file is
reproducible and a half-finished pass can be regenerated without losing its
order.

## What a label means

`same_place` is `y`, `n`, or `?`.

`?` is not a gap in the work - it is a finding, and it has to be available or it
becomes a coin-flip recorded as truth. Two accounts, one town, no street
address, names a syllable apart: the export cannot settle it and neither can the
reviewer. Pairs marked `?` are excluded from both precision and recall and
**reported as a count**, because a book where forty per cent of the hard pairs
are unanswerable is telling us something about the export that no resolver
score will.
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path

from ml.agt1.book import Account
from ml.agt1.pool import Pair, Pool, pair_key

SAME = "y"
DIFFERENT = "n"
UNDECIDABLE = "?"
_VERDICTS = {SAME, DIFFERENT, UNDECIDABLE}

# Fixed so regenerating the file does not reshuffle a half-finished pass.
SHUFFLE_SEED = 20260922

FIELDNAMES = (
    "same_place",
    "note",
    "account_a",
    "name_a",
    "town_a",
    "postcode_a",
    "stops_a",
    "account_b",
    "name_b",
    "town_b",
    "postcode_b",
    "stops_b",
    "pooled_because",
)


@dataclass
class GoldLabels:
    """What a person decided, and what they could not decide."""

    same: set[Pair]
    different: set[Pair]
    undecidable: set[Pair]
    unlabelled: set[Pair]

    @property
    def judged(self) -> set[Pair]:
        """Pairs with a usable verdict - the only ones anything is scored on."""
        return self.same | self.different

    @property
    def coverage(self) -> float:
        """Share of pooled pairs a person has actually reached.

        Reported on every run. A precision figure from a third-labelled file is
        not a preliminary result, it is a different measurement, and the only
        thing that stops it being quoted as the first is this number sitting
        next to it.
        """
        total = len(self.same) + len(self.different) + len(self.undecidable) + len(self.unlabelled)
        if total == 0:
            return 0.0
        return round(1 - len(self.unlabelled) / total, 3)


def write_label_file(
    path: str | Path, *, accounts: list[Account], pool: Pool
) -> int:
    """Emit the pairs to be judged, shuffled, with the verdict column blank.

    Refuses to overwrite. The file is somebody's day of work and this function
    is one typo away from being run twice.
    """
    target = Path(path)
    if target.exists():
        raise FileExistsError(
            f"{target} already exists - labels are written once by hand. "
            "Move it aside deliberately if you really mean to start again."
        )

    by_id = {account.receiver_id: account for account in accounts}
    rows = []
    for left, right in pool.pairs:
        a, b = by_id[left], by_id[right]
        rows.append(
            {
                "same_place": "",
                "note": "",
                "account_a": a.receiver_id,
                "name_a": a.name,
                "town_a": a.city,
                "postcode_a": a.zip_code,
                "stops_a": a.stops,
                "account_b": b.receiver_id,
                "name_b": b.name,
                "town_b": b.city,
                "postcode_b": b.zip_code,
                "stops_b": b.stops,
                "pooled_because": "; ".join(pool.reasons[pair_key(left, right)]),
            }
        )

    rows.sort(key=lambda row: (row["account_a"], row["account_b"]))
    random.Random(SHUFFLE_SEED).shuffle(rows)

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def read_labels(path: str | Path) -> GoldLabels:
    """Read the filled-in file. An unrecognised verdict is an error, not a no.

    A reviewer typing `Y`, `yes` or `1` means yes and gets it. A reviewer typing
    `maybe` means something this scale cannot hold, and silently counting it as
    `n` would turn their uncertainty into a resolver's false positive.
    """
    labels = GoldLabels(same=set(), different=set(), undecidable=set(), unlabelled=set())
    aliases = {
        "y": SAME, "yes": SAME, "1": SAME, "true": SAME, "same": SAME,
        "n": DIFFERENT, "no": DIFFERENT, "0": DIFFERENT, "false": DIFFERENT, "different": DIFFERENT,
        "?": UNDECIDABLE, "unknown": UNDECIDABLE, "unclear": UNDECIDABLE,
    }

    with open(path, newline="", encoding="utf-8-sig") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            pair = pair_key(row["account_a"].strip(), row["account_b"].strip())
            raw = (row.get("same_place") or "").strip().casefold()
            if not raw:
                labels.unlabelled.add(pair)
                continue
            verdict = aliases.get(raw)
            if verdict is None:
                raise ValueError(
                    f"{path} line {line}: same_place is {row['same_place']!r}. "
                    f"Use one of {sorted(_VERDICTS)} - an unreadable verdict "
                    "must not be quietly counted as 'no'."
                )
            {SAME: labels.same, DIFFERENT: labels.different, UNDECIDABLE: labels.undecidable}[
                verdict
            ].add(pair)

    return labels
