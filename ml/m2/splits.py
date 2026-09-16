"""Two splits, because they answer two different questions (rule 2).

> *"A random split puts the same dock on both sides and lies. Chronological
> split for the headline number; entire unseen locations for the cold-start
> number - which is what a new customer actually experiences in week one."*

**Chronological** is the honest version of "how well does this work". Train on
what came before, score on what came after. A random split lets the model learn
December from December, and no deployment ever gets to do that.

**Held-out locations** is the number a prospect is actually buying. Every
feature that carries real signal here is a location history, and a new customer
has none - so a model scored only on docks it has seen is quoting a number that
will not be available in week one. `MODEL_AND_DATA_BRIEF.md` rule (3) records
what happened when we did not check: the p90 model covered **66.5%** of
cold-start cases after promising 90%.

**Locations are held out whole, and by hash.** Not by a slice of a sorted list,
which would correlate with whatever order the ids were generated in, and not by
a shuffled sample, which would move every time somebody changed a seed
upstream. A hash of the location id is stable: the same dock is on the same side
of the split next month, so two runs are comparable.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ml.m2.features import FeatureRow


@dataclass(frozen=True)
class Split:
    name: str
    train: list[FeatureRow]
    test: list[FeatureRow]
    # What this split is a fair test of, in one line, carried alongside the
    # numbers so a score cannot be quoted without its question.
    question: str


def chronological(rows: list[FeatureRow], *, train_fraction: float = 0.7) -> Split:
    """Everything before a cut date trains; everything after is the test.

    Cut on the date rather than on the row index so a single busy day cannot
    straddle the boundary and put the morning in train and the afternoon in
    test - which would be a small, invisible version of the leak rule (1) is
    about.
    """
    ordered = sorted(rows, key=lambda r: (r.delivered_on, r.order_id))
    days = sorted({r.delivered_on for r in ordered})
    cut = days[int(len(days) * train_fraction)]
    return Split(
        name="chronological",
        train=[r for r in ordered if r.delivered_on < cut],
        test=[r for r in ordered if r.delivered_on >= cut],
        question="How well does this work on next month, having seen last month?",
    )


def held_out_locations(rows: list[FeatureRow], *, holdout_fraction: float = 0.2) -> Split:
    """Whole docks, never seen in training. The cold-start number."""
    def held_out(location_id: str) -> bool:
        digest = hashlib.sha256(f"m2-holdout:{location_id}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / float(1 << 64) < holdout_fraction

    return Split(
        name="held_out_locations",
        train=[r for r in rows if not held_out(r.location_id)],
        test=[r for r in rows if held_out(r.location_id)],
        question="What does a new customer see in week one, on docks we have never visited?",
    )


def control_arm_only(rows: list[FeatureRow], *, arm: str = "control") -> list[FeatureRow]:
    """The unconfounded slice.

    `MODEL_AND_DATA_BRIEF.md` §M2: the label *"requires the randomised hold arm
    (EXP-1). Unobtainable by watching."* Lateness outside the arm is produced by
    our own batching policy, so the late population there is a sample our policy
    chose. This is the slice where it is not.

    It is almost always too small to train on - see `gates.py`, which measures
    exactly how small and refuses to let the number go unstated.
    """
    return [r for r in rows if r.arm == arm]
