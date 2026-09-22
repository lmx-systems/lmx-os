#!/usr/bin/env python3
"""Run AGT-1, the resolution bake-off, against the real export.

    python scripts/agt1_bakeoff.py labels          # write the file to hand-label
    python scripts/agt1_bakeoff.py score           # score whoever can run
    python scripts/agt1_bakeoff.py score --agent --release-approved

**`labels` first, and then a person.** The truth file is written once, by hand,
and nothing here can produce it: generating labels with a model would score the
challenger against itself, and scoring against the incumbent's own output would
hand it a precision of 1.000 by construction.

Everything reads and writes inside `lmx-dwell/`, which is gitignored because it
holds the design partner's operational book. This prints account ids and never a
customer name - the same discipline `scripts/analyze_real_export.py` keeps - but
the label file itself necessarily holds names and towns, because that is the
question a reviewer is answering. **It stays where it is written.**

`--agent` sends those names and towns to a model vendor. `--release-approved` is
required alongside it and is not a convenience flag: 0.2, the data-rights and
pooling-consent clause, is the unsigned thing that would govern it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.agt1.agent import MODEL, AgentResolver, availability  # noqa: E402
from ml.agt1.book import load_book  # noqa: E402
from ml.agt1.gold import read_labels, write_label_file  # noqa: E402
from ml.agt1.pool import pool_for_bakeoff  # noqa: E402
from ml.agt1.report import render  # noqa: E402
from ml.agt1.resolvers import DeterministicResolver  # noqa: E402
from ml.agt1.score import disagreements, score  # noqa: E402

DEFAULT_BOOK = "lmx-dwell/out/stops_timing.csv"
DEFAULT_LABELS = "lmx-dwell/out/identity/identity_truth.csv"


def _labels(args: argparse.Namespace) -> int:
    book = load_book(args.book)
    pool = pool_for_bakeoff(book)
    written = write_label_file(args.labels, accounts=book, pool=pool)
    print(f"{len(book)} accounts, {written} pairs to judge -> {args.labels}")
    print("\nFill in `same_place` with y / n / ?, one row at a time.")
    print("`?` is a real answer: two names a syllable apart in one town with no")
    print("street address is not a coin-flip somebody should record as a decision.")
    if pool.unlocatable:
        print(
            f"\n{len(pool.unlocatable)} accounts carry neither town nor postcode "
            "and can only be reached through a shared account root or a rare word:"
        )
        for receiver_id in sorted(pool.unlocatable):
            print(f"  {receiver_id}")
    return 0


def _score(args: argparse.Namespace) -> int:
    if not Path(args.labels).exists():
        print(
            f"no truth file at {args.labels}.\n"
            "Run `labels` first, then fill it in by hand. There is nothing to "
            "score a resolver against until somebody has.",
            file=sys.stderr,
        )
        return 1

    book = load_book(args.book)
    by_id = {account.receiver_id: account for account in book}
    pool = pool_for_bakeoff(book)
    labels = read_labels(args.labels)
    pairs = sorted(pool.pairs)

    cards, unavailable = [], {}
    contestants = [DeterministicResolver()]
    if args.agent:
        blocked = availability(release_approved=args.release_approved)
        if blocked:
            unavailable["agent"] = blocked.reason
        else:
            contestants.append(AgentResolver(release_approved=True, model=args.model))
    else:
        unavailable["agent"] = "not asked for — pass --agent"

    for resolver in contestants:
        cards.append(
            score(
                resolver=resolver.name,
                proposals=resolver.propose(by_id, pairs),
                labels=labels,
                accounts=book,
            )
        )

    head_to_head = (
        disagreements(cards[0], cards[1], labels) if len(cards) == 2 else None
    )
    print(
        render(
            accounts=book,
            pool=pool,
            labels=labels,
            cards=cards,
            unavailable=unavailable,
            head_to_head=head_to_head,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", default=DEFAULT_BOOK, help="the whole-book export")
    parser.add_argument("--labels", default=DEFAULT_LABELS, help="the hand-labelled truth")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("labels", help="write the file a person hand-labels")

    scoring = sub.add_parser("score", help="score the resolvers against the truth")
    scoring.add_argument("--agent", action="store_true", help="run the agent challenger too")
    scoring.add_argument(
        "--release-approved",
        action="store_true",
        help="acknowledge that the partner's account names and towns leave our "
        "infrastructure for a model vendor (see 0.2)",
    )
    scoring.add_argument("--model", default=MODEL)

    args = parser.parse_args()
    return _labels(args) if args.command == "labels" else _score(args)


if __name__ == "__main__":
    raise SystemExit(main())
