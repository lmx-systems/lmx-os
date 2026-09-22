"""The bake-off written out, including the parts that did not run.

One rule governs the layout: **a contestant that could not run gets a row
saying why, not an omission.** `ml/m1/`'s gate learned this the expensive way -
`libomp` was missing through four separate mentions of `requirements-ml.txt`
before anybody found out - and a report that silently prints one column when it
was built to print two is how an unrun challenger becomes a challenger that
lost.
"""
from __future__ import annotations

from ml.agt1.book import Account
from ml.agt1.gold import GoldLabels
from ml.agt1.pool import Pool
from ml.agt1.score import Disagreement, Scorecard


def _pct(value: float | None) -> str:
    return "     -" if value is None else f"{value:6.3f}"


def render(
    *,
    accounts: list[Account],
    pool: Pool,
    labels: GoldLabels,
    cards: list[Scorecard],
    unavailable: dict[str, str],
    head_to_head: Disagreement | None = None,
    examples: int = 8,
) -> str:
    """The whole report as text, ready for a terminal or a file."""
    total_pairs = len(accounts) * (len(accounts) - 1) // 2
    lines: list[str] = []
    out = lines.append

    out("AGT-1 — the resolution bake-off")
    out("=" * 64)
    out("")
    out(f"accounts in the book        {len(accounts)}")
    out(f"pairs in the whole space    {total_pairs}")
    out(f"pairs pooled for review     {len(pool.pairs)}  ({len(pool.pairs) / total_pairs:.1%})")
    if pool.unlocatable:
        out(
            f"accounts with no town and no postcode   {len(pool.unlocatable)}"
            "  (blockable only by account root or a rare word)"
        )
    for reason, size in sorted(pool.oversized.items()):
        out(f"block refused as a region   {reason} ({size} accounts)")
    out("")
    out(f"labelled                    {labels.coverage:.1%} of pooled pairs")
    out(f"  same place                {len(labels.same)}")
    out(f"  different places          {len(labels.different)}")
    out(f"  could not be decided      {len(labels.undecidable)}")
    out(f"  not yet reached           {len(labels.unlabelled)}")
    if labels.unlabelled:
        out("")
        out("  ! Every figure below is a statement about the labelled part only.")
    out("")

    # Δdocks compares a closure over *every* proposal against a closure over
    # the labelled yeses. Those are the same population only when the file is
    # finished; on a part-labelled file the difference is mostly unlabelled
    # pairs, and printing it would read as a resolver's error.
    known = bool(labels.same) and not labels.unlabelled
    out("resolver       precision  recall      f1   docks  Δdocks     FP     FN")
    out("-" * 72)
    for card in cards:
        delta = f"{card.dock_count_error:>+6}" if known else "     ?"
        out(
            f"{card.resolver:<13}"
            f"{_pct(card.precision)}  {_pct(card.recall)}  {_pct(card.f1)}"
            f"  {card.implied_dock_count:>6}  {delta}"
            f"  {len(card.false_positives):>5}  {len(card.false_negatives):>5}"
        )
    for name, reason in sorted(unavailable.items()):
        out(f"{name:<14}  DID NOT RUN — {reason}")
    out("")
    if cards:
        out("")
        if known:
            out(f"true dock count behind these accounts   {cards[0].true_dock_count}")
        elif labels.unlabelled:
            out(
                "true dock count behind these accounts   not yet — `docks` above "
                "closes over every proposal while the truth closes over the "
                "labelled yeses only, and those are one population only when the "
                "file is finished"
            )
        else:
            out(
                "true dock count behind these accounts   unknown — nothing is "
                "labelled `same place`, so `docks` above is each resolver's "
                "claim and `Δdocks` has nothing to measure against"
            )
        out("")

    for card in cards:
        out(f"{card.resolver}: {card.stops_wrongly_merged} stops wrongly merged, "
            f"{card.stops_wrongly_split} stops left wrongly split")
        if card.largest_implied_cluster > 2:
            out(
                f"{card.resolver}: its largest implied dock holds "
                f"{card.largest_implied_cluster} accounts — every edge in a chain "
                "can be defensible while the closure is not, and no pairwise "
                "score can see this"
            )
        if card.on_undecidable:
            out(
                f"{card.resolver}: proposed {len(card.on_undecidable)} pairs the "
                "reviewer could not decide — scored against neither"
            )
    out("")

    if head_to_head is not None:
        left, right = cards[0].resolver, cards[1].resolver
        out("Head to head, on pairs a person judged")
        out("-" * 64)
        out(f"{left} right, {right} wrong    {len(head_to_head.only_left_correct)}")
        out(f"{right} right, {left} wrong    {len(head_to_head.only_right_correct)}")
        out(f"both wrong                     {len(head_to_head.both_wrong)}")
        for title, pairs in (
            (f"only {left} gets these right", head_to_head.only_left_correct),
            (f"only {right} gets these right", head_to_head.only_right_correct),
        ):
            if not pairs:
                continue
            out("")
            out(f"  {title}:")
            for pair in sorted(pairs)[:examples]:
                out(f"    {pair[0]}  ~  {pair[1]}")
            if len(pairs) > examples:
                out(f"    ... and {len(pairs) - examples} more")

    return "\n".join(lines)
