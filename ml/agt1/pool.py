"""Which pairs a person is asked to judge, and why recall has a denominator.

229 accounts make **26,106 pairs**. Nobody labels 26,106 pairs, so the honest
move is not to pretend otherwise but to say exactly which pairs were considered
and what that costs the numbers.

## The pool, and the guarantee it does and does not give

A pair enters the pool if the two accounts share **an account root**, **a
postcode**, or **a rare word in the name** - the three ways this export
actually links two records, taken from `account_signals`, which took them from
the ad-hoc pass that found duplicates here in the first place.

That is 1,856 pairs on the real book: seven per cent of the space, and a day of
a person's attention rather than a fortnight.

**Then every contestant's proposals are unioned in**, whether or not blocking
caught them. This is not a detail. Run the deterministic resolver over all
26,106 pairs and it proposes 97, of which **20 fall outside the blocks** - the
weak name-similarity tier reaches pairs that share no root, no postcode and no
rare token. A pool missing them would leave twenty of the incumbent's own calls
unjudged, and the number that came out would be a precision score with holes in
it, reported as though it had none.

So:

- **Precision is exact.** Every pair a resolver proposes is in the pool and
  therefore labelled, so every proposal is scored right or wrong.
- **Recall is an upper bound.** A pair that is genuinely one dock, that no
  contestant proposed, and that shares no root, no postcode and no rare word, is
  invisible to this measurement. Both resolvers are flattered equally and the
  comparison between them survives; the absolute figure does not, and must never
  be quoted without this sentence.

## The rows no pool can reach

Four accounts carry **neither town nor postcode**, and one carries a town in the
postcode column. They are listed separately rather than dropped: they cannot be
blocked on location, so their only route into the pool is a shared root or a
rare word, and if they have neither they are unjudgeable by anything here. That
is a property of the export, not of a resolver, and it belongs in the report
rather than in a footnote.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from app.identity.account_signals import distinctive_tokens, parse_account_ref
from ml.agt1.book import Account

# A name token shared by more than this many accounts is a word about the trade,
# not a word about a business. `_COMMON_TOKENS` in `account_signals` catches the
# obvious ones by hand; this catches the ones that are common *in this book*
# without anybody having to predict them - a town name used as a brand, a
# franchise, a surname three families in the county share.
#
# Twelve is chosen so the blocking stays sub-quadratic while the largest token
# block stays smaller than the largest postcode block, which is 36. A token that
# links more accounts than a postcode does is not identifying.
RARE_TOKEN_MAX_ACCOUNTS = 12

# A block bigger than this is not a link, it is a region. Nothing in the real
# book hits it - the largest postcode holds 36 accounts - but a second customer
# with one warehouse postcode for four hundred accounts would otherwise turn the
# pool back into the full quadratic and silently take a week of somebody's life.
MAX_BLOCK_SIZE = 60

Pair = tuple[str, str]


def pair_key(left: str, right: str) -> Pair:
    """A pair identified the same way whichever order it arrived in."""
    return (left, right) if left <= right else (right, left)


@dataclass
class Pool:
    """The pairs a person is asked about, and the reasons they got in."""

    pairs: set[Pair] = field(default_factory=set)
    # Pair -> the blocks that caught it. A reviewer reading "same account root"
    # is doing a different job from one reading "both in 07631", and the pool
    # knows which, so the label file can say so.
    reasons: dict[Pair, list[str]] = field(default_factory=dict)
    # Accounts with neither town nor postcode. Not an error and not excluded -
    # reported, because a resolver cannot be blamed for them and a reader should
    # not have to discover them.
    unlocatable: list[str] = field(default_factory=list)
    # Blocks refused for being a region rather than a link.
    oversized: dict[str, int] = field(default_factory=dict)

    def add(self, left: str, right: str, reason: str) -> None:
        key = pair_key(left, right)
        self.pairs.add(key)
        reasons = self.reasons.setdefault(key, [])
        if reason not in reasons:
            reasons.append(reason)


def build_pool(accounts: list[Account]) -> Pool:
    """Block the book into the pairs worth a person's time."""
    pool = Pool()

    token_accounts: Counter = Counter()
    for account in accounts:
        for token in distinctive_tokens(account.name):
            token_accounts[token] += 1

    blocks: dict[str, set[str]] = defaultdict(set)
    for account in accounts:
        reference = parse_account_ref(account.receiver_id)
        if reference:
            blocks[f"account root {reference.root}"].add(account.receiver_id)
        if account.zip_code:
            blocks[f"postcode {account.zip_code}"].add(account.receiver_id)
        for token in distinctive_tokens(account.name):
            if token_accounts[token] <= RARE_TOKEN_MAX_ACCOUNTS:
                blocks[f"both names contain '{token}'"].add(account.receiver_id)
        if not account.city and not account.zip_code:
            pool.unlocatable.append(account.receiver_id)

    for reason, members in blocks.items():
        if len(members) > MAX_BLOCK_SIZE:
            pool.oversized[reason] = len(members)
            continue
        for left, right in combinations(sorted(members), 2):
            pool.add(left, right, reason)

    return pool


def pool_for_bakeoff(accounts: list[Account]) -> Pool:
    """The blocks, plus every pair the incumbent would propose anywhere.

    The union is the whole point and it does not belong in a script. Twenty of
    the deterministic resolver's calls on the real book fall outside every
    block - the weak name-similarity tier reaches pairs sharing no root, no
    postcode and no rare word - and a pool without them would leave twenty of
    its own proposals unlabelled while reporting a precision as though it had
    scored them.

    Imported here rather than at module scope: `resolvers` imports `pool` for
    `pair_key`, and the honest way to break that cycle is to admit the
    dependency runs one way at import time and the other way at call time.
    """
    from ml.agt1.resolvers import full_space_proposals

    pool = build_pool(accounts)
    for left, right in full_space_proposals(accounts):
        pool.add(left, right, "the deterministic resolver proposes it")
    return pool
