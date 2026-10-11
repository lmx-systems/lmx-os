"""Training never sees a frozen test site (`DE-8`, decision D-TEST).

The test sites are drawn once, in the database, and exported to a file by
`scripts/freeze_test_sites.py export` - `ml/` shares no code with `app/`. Every
modality run splits on that file rather than on a hash made per run, which is
what `ml/m1/` and `ml/m2/` do and what let a site be test in one run and training
in the next.

`assert_no_test_sites` is the refusal: every training set passes through it, so
a test site that reached one is an exception, not a quietly better score.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path


class TestSiteInTraining(AssertionError):
    """A frozen test site reached a training set."""

    __test__ = False  # not a pytest test class, despite the name


def load_test_sites(path: Path) -> frozenset[str]:
    """The location ids of every frozen test site, from the export."""
    document = json.loads(Path(path).read_text())
    return frozenset(site["location_id"] for site in document["test_sites"])


def assert_no_test_sites(training: Iterable[Mapping], test_sites: frozenset[str]) -> None:
    """Raise if any training record is at a frozen test site."""
    leaked = sorted({r["location_id"] for r in training if r.get("location_id") in test_sites})
    if leaked:
        raise TestSiteInTraining(
            f"{len(leaked)} frozen test site(s) in a training set, e.g. {leaked[0]}"
        )


def split_on_test_sites(
    records: Iterable[Mapping], test_sites: frozenset[str]
) -> tuple[list[Mapping], list[Mapping]]:
    """(training, test): a record is test exactly when its site is frozen.

    A record with no site cannot be at a test site, so it trains. The training
    side is checked on the way out, so a caller cannot get a leaky split from here.
    """
    training, test = [], []
    for record in records:
        (test if record.get("location_id") in test_sites else training).append(record)
    assert_no_test_sites(training, test_sites)
    return training, test
