"""Scoring a resolver against the hand-labelled truth, three ways.

The done-when asks for *"precision and recall on merge decisions ... plus the
cases each gets right that the other does not."* Pairwise precision and recall
are the first of those and are not sufficient on their own, for a reason worth
stating.

## Merges are transitive and pairwise scores are not

A resolver that says `A=B` and `B=C` has said there is one dock, whether or not
it ever mentioned `A=C`. Two resolvers can score identically pair-for-pair and
imply a different **dock count** - which is the number the roadmap item is
actually about: *"the true physical dock count behind the 230 account IDs."*

So every resolver is also scored on the clustering its proposals imply, by
transitive closure, against the clustering the labels imply. Both are reported.
When they disagree, the pairwise number is the one that is misleading.

## Stops at stake

A wrong merge between two accounts worth 400 stops and 371 stops corrupts a
per-dock statistic that a dwell model trains on. The same wrong merge between
two one-stop accounts is a rounding error. Error counts treat them as equal and
this does not: every error total is reported in pairs **and** in the stops the
smaller side of the pair carries, which is what moves if the call is wrong.

## `?` is excluded, loudly

Pairs a reviewer could not decide score against nobody. They are counted and
printed, because a book where the undecidables outnumber the yeses is a finding
about the export - no street address, one town, near-identical names - and not
a resolver's failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ml.agt1.book import Account
from ml.agt1.gold import GoldLabels
from ml.agt1.pool import Pair
from ml.agt1.resolvers import Proposal


def _clusters(pairs: set[Pair], members: list[str]) -> list[set[str]]:
    """Transitive closure: the docks a set of merge claims implies."""
    parent = {member: member for member in members}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in pairs:
        if left not in parent or right not in parent:
            continue
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    grouped: dict[str, set[str]] = {}
    for member in members:
        grouped.setdefault(find(member), set()).add(member)
    return list(grouped.values())


@dataclass
class Scorecard:
    """One resolver's performance against the labels."""

    resolver: str
    true_positives: set[Pair] = field(default_factory=set)
    false_positives: set[Pair] = field(default_factory=set)
    false_negatives: set[Pair] = field(default_factory=set)
    # Proposals on pairs the reviewer marked `?`. Not errors and not successes.
    on_undecidable: set[Pair] = field(default_factory=set)
    implied_dock_count: int = 0
    true_dock_count: int = 0
    # The biggest thing the resolver's proposals weld together, by transitive
    # closure. Printed because pairwise scores cannot see it and it is where
    # pairwise merging fails: every edge looks defensible on its own and the
    # closure is a dock with fourteen unrelated businesses in it.
    largest_implied_cluster: int = 0
    stops_wrongly_merged: int = 0
    stops_wrongly_split: int = 0

    @property
    def precision(self) -> float | None:
        proposed = len(self.true_positives) + len(self.false_positives)
        if proposed == 0:
            return None
        return round(len(self.true_positives) / proposed, 3)

    @property
    def recall(self) -> float | None:
        actual = len(self.true_positives) + len(self.false_negatives)
        if actual == 0:
            return None
        return round(len(self.true_positives) / actual, 3)

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or precision + recall == 0:
            return None
        return round(2 * precision * recall / (precision + recall), 3)

    @property
    def dock_count_error(self) -> int:
        """Docks over-counted (positive) or under-counted (negative).

        Positive means the resolver left docks split that are one place;
        negative means it welded distinct places together. The sign matters:
        splitting halves a dock's stop count, welding invents a dock with two
        businesses' dwell in it, and only the second is invisible afterwards.
        """
        return self.implied_dock_count - self.true_dock_count


def score(
    *,
    resolver: str,
    proposals: dict[Pair, Proposal],
    labels: GoldLabels,
    accounts: list[Account],
) -> Scorecard:
    """Judge one resolver's proposals against what a person decided."""
    stops = {account.receiver_id: account.stops for account in accounts}
    members = sorted(stops)
    proposed = set(proposals)

    card = Scorecard(resolver=resolver)
    card.true_positives = proposed & labels.same
    card.false_positives = proposed & labels.different
    card.false_negatives = labels.same - proposed
    card.on_undecidable = proposed & labels.undecidable

    # The smaller side is what moves: merging a 400-stop dock into a 3-stop one
    # puts three stops somewhere wrong, not four hundred.
    card.stops_wrongly_merged = sum(
        min(stops.get(left, 0), stops.get(right, 0)) for left, right in card.false_positives
    )
    card.stops_wrongly_split = sum(
        min(stops.get(left, 0), stops.get(right, 0)) for left, right in card.false_negatives
    )

    implied = _clusters(proposed, members)
    card.implied_dock_count = len(implied)
    card.largest_implied_cluster = max((len(group) for group in implied), default=0)
    card.true_dock_count = len(_clusters(labels.same, members))
    return card


@dataclass
class Disagreement:
    """Where the two resolvers differ, on pairs a person actually judged."""

    only_left_correct: set[Pair] = field(default_factory=set)
    only_right_correct: set[Pair] = field(default_factory=set)
    both_wrong: set[Pair] = field(default_factory=set)


def disagreements(left: Scorecard, right: Scorecard, labels: GoldLabels) -> Disagreement:
    """*"The cases each gets right that the other does not."*

    Computed over judged pairs only, and over **every** judged pair rather than
    only the proposed ones - a pair both resolvers correctly declined is a
    shared success, and a pair one declined correctly while the other proposed
    it wrongly is exactly the kind of case this report exists to surface.
    """
    def correct(card: Scorecard) -> set[Pair]:
        proposed = card.true_positives | card.false_positives | card.on_undecidable
        declined_rightly = labels.different - proposed
        return card.true_positives | declined_rightly

    left_correct, right_correct = correct(left), correct(right)
    return Disagreement(
        only_left_correct=left_correct - right_correct,
        only_right_correct=right_correct - left_correct,
        both_wrong=labels.judged - left_correct - right_correct,
    )
