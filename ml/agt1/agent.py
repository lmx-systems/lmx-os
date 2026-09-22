"""The challenger: a model asked the same question the reviewer is asked.

`ml/m1/challenger.py`'s two rules, unchanged, because they earned themselves
there. **Optional by construction** - imported lazily, reported unavailable
rather than raising, so a gate that cannot run does not become a gate that
stops running. And **the handicap is stated in the module, not discovered in the
result**.

## It is given exactly the reviewer's evidence, and nothing else

Both account ids, both names, both towns, both postcodes, both stop counts, and
nothing about what the deterministic resolver concluded. The same anchoring
argument as `gold.py`, one degree stronger: a challenger shown the incumbent's
answer is not a second opinion, it is a review of the first, and the bake-off
would be measuring agreement.

It is *not* given the blocking reason either. "These two share an account root"
is a conclusion drawn from the ids, and a resolver that can read the ids can
draw it.

## Two things stand between this and a run

**No key.** There is no LLM credential in this repository and no LLM dependency
in `requirements.txt`; this speaks the Messages API over `httpx`, which is
already a dependency, so running it costs a key and not a package.

**A data-rights question that is not ours.** Every pair sent is two of the
design partner's customers, by name and town. That is their operational book
leaving our infrastructure for a third party, and `ROADMAP_1.5.md` 0.2 - the
data-rights and pooling-consent clause - is exactly the unsigned thing that
would govern it. So the caller must pass `release_approved=True` and the script
makes somebody type it. A flag is not consent; it is a place where the absence
of consent stops being invisible.

Until both exist the bake-off runs one-sided, and `report.py` says so on the
line where the challenger's numbers would be rather than omitting the row.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ml.agt1.book import Account
from ml.agt1.pool import Pair, pair_key
from ml.agt1.resolvers import Proposal

MODEL = "claude-opus-5"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# Pairs per request. Large enough that 1,876 pairs is tens of calls rather than
# thousands, small enough that one malformed response costs a batch and not the
# run. Deliberately not "all of them": a single request holding the whole book
# invites the model to reason about the book instead of about the pair.
BATCH_SIZE = 25

TIER_AGENT = "AGENT"

_SYSTEM = """You are resolving customer identity for a delivery operator.

Each item gives two account records from one distributor's book. Decide whether
they are the same PHYSICAL DELIVERY DOCK - one place a driver is sent - or two
different places.

What you have is all there is. There is no street address in this export: only
an account id, a business name, a town and a postcode. Account ids are shaped
ROOT/BRANCH-SUFFIX and the structure is the distributor's own bookkeeping.

Answer "same", "different", or "unclear". Use "unclear" when the evidence
genuinely does not settle it - a guess recorded as a decision is worse than an
abstention, because a wrong merge is invisible once applied.

Reply with JSON only: a list of {"id": <item id>, "verdict": ..., "why": <one
short sentence>}. No prose outside the JSON."""


@dataclass(frozen=True)
class Unavailable:
    """Why the challenger could not run. Printed, never swallowed."""

    reason: str


def availability(*, release_approved: bool) -> Unavailable | None:
    """Whether the agent resolver can run at all, and why not if it cannot."""
    if not release_approved:
        return Unavailable(
            "the design partner's account names and towns would be sent to a "
            "model vendor, and the 0.2 data-rights clause that would govern "
            "that is unsigned - pass --release-approved only once it is"
        )
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Unavailable("ANTHROPIC_API_KEY is not set")
    try:
        import httpx  # noqa: F401
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        return Unavailable("httpx is not installed")
    return None


def _item(index: int, left: Account, right: Account) -> dict:
    return {
        "id": index,
        "a": {
            "account": left.receiver_id,
            "name": left.name,
            "town": left.city,
            "postcode": left.zip_code,
            "stops": left.stops,
        },
        "b": {
            "account": right.receiver_id,
            "name": right.name,
            "town": right.city,
            "postcode": right.zip_code,
            "stops": right.stops,
        },
    }


class AgentResolver:
    """One model call per batch of pairs, verdicts parsed back per pair."""

    name = "agent"

    def __init__(self, *, release_approved: bool = False, model: str = MODEL, transport=None):
        self.release_approved = release_approved
        self.model = model
        # Injected in tests so the parsing and batching are covered without a
        # key and without sending anybody's book anywhere. A recorded reply is
        # a fixture; a live call in a test suite is a bill.
        self._transport = transport

    def propose(
        self, accounts: dict[str, Account], pairs: list[Pair]
    ) -> dict[Pair, Proposal]:
        if self._transport is None:
            blocked = availability(release_approved=self.release_approved)
            if blocked:
                raise RuntimeError(f"agent resolver unavailable: {blocked.reason}")

        verdicts: dict[Pair, Proposal] = {}
        ordered = sorted(pairs)
        for start in range(0, len(ordered), BATCH_SIZE):
            batch = ordered[start : start + BATCH_SIZE]
            items = [
                _item(index, accounts[left], accounts[right])
                for index, (left, right) in enumerate(batch)
            ]
            for answer in self._ask(items):
                index = answer.get("id")
                if not isinstance(index, int) or not 0 <= index < len(batch):
                    continue
                if str(answer.get("verdict", "")).strip().casefold() != "same":
                    continue
                left, right = batch[index]
                verdicts[pair_key(left, right)] = Proposal(
                    tier=TIER_AGENT, reason=str(answer.get("why", "")).strip()
                )
        return verdicts

    def _ask(self, items: list[dict]) -> list[dict]:
        body = {
            "model": self.model,
            "max_tokens": 4096,
            "system": _SYSTEM,
            "messages": [{"role": "user", "content": json.dumps(items)}],
        }
        if self._transport is not None:
            return _parse(self._transport(body))

        import httpx

        response = httpx.post(
            API_URL,
            json=body,
            timeout=120.0,
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
        )
        response.raise_for_status()
        return _parse(response.json())


def _parse(payload: dict) -> list[dict]:
    """Pull the verdict list out of a Messages reply.

    A batch that comes back unparseable yields no verdicts rather than raising.
    That is the conservative direction: the agent is credited with nothing for
    that batch, which costs it recall, and the alternative - abandoning the run
    - would lose every batch that did parse.
    """
    text = "".join(
        block.get("text", "")
        for block in payload.get("content", [])
        if block.get("type") == "text"
    ).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
