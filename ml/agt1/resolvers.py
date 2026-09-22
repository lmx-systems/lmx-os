"""The contestants, and the rule that they are asked the same questions.

*"Run the existing deterministic resolver and an agent resolver on identical
input."* Identical is doing real work in that sentence. The deterministic
resolver can be run over all 26,106 pairs for free and the agent cannot be, so
the naive comparison gives one contestant a wider net and then reports the
difference as skill.

The fix is `pool.py`: every pair either resolver would ever propose is pulled
into the pool, and then **both are asked about exactly the pool**. The
deterministic resolver's free pass over the full space is used to *build* the
question set, never to answer more of it than its opponent gets.

## A resolver decides, it does not merge

Both contestants return a verdict per pair and nothing applies anything. That is
`IDN-2`'s rule unchanged - *"this module proposes and never decides"* - and it
is also what makes the bake-off measurable: a merge is a side effect, and a side
effect cannot be scored against a label.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Protocol

from app.identity.account_signals import why_these_accounts_might_be_one_place
from ml.agt1.book import Account
from ml.agt1.pool import Pair, pair_key


@dataclass(frozen=True)
class Proposal:
    """One resolver's claim that two accounts are one dock."""

    tier: str
    reason: str


class Resolver(Protocol):
    """Anything that can answer *"is this pair one place"* for a set of pairs."""

    name: str

    def propose(
        self, accounts: dict[str, Account], pairs: list[Pair]
    ) -> dict[Pair, Proposal]:
        """Verdicts for the pairs it was asked about, keyed by pair.

        A pair left out of the returned mapping is a "no". Returning only the
        yeses rather than a verdict for every pair keeps a resolver that
        proposes six pairs from having to enumerate 1,876 refusals.
        """
        ...


class DeterministicResolver:
    """`IDN-2` as it ships today: account structure, postcode, rare words.

    A thin adapter, deliberately. If this reimplemented the rules the bake-off
    would be measuring a copy of the incumbent rather than the incumbent, and
    the copy would drift the first time somebody tuned a threshold.
    """

    name = "deterministic"

    def propose(
        self, accounts: dict[str, Account], pairs: list[Pair]
    ) -> dict[Pair, Proposal]:
        verdicts: dict[Pair, Proposal] = {}
        for left, right in pairs:
            a, b = accounts[left], accounts[right]
            found = why_these_accounts_might_be_one_place(
                ref_a=a.receiver_id,
                name_a=a.name,
                address_a=a.address,
                ref_b=b.receiver_id,
                name_b=b.name,
                address_b=b.address,
            )
            if found:
                tier, reason = found
                verdicts[pair_key(left, right)] = Proposal(tier=tier, reason=reason)
        return verdicts


def full_space_proposals(accounts: list[Account]) -> dict[Pair, Proposal]:
    """Every pair the deterministic resolver would propose, over all pairs.

    Used to *build* the pool, not to score. Quadratic and cheap: 26,106 pairs of
    string comparisons on the real book, under a second.
    """
    by_id = {account.receiver_id: account for account in accounts}
    every_pair = [
        pair_key(left.receiver_id, right.receiver_id)
        for left, right in combinations(accounts, 2)
    ]
    return DeterministicResolver().propose(by_id, every_pair)
