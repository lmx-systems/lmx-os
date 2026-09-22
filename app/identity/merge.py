"""Proposing, confirming and reversing the claim that two docks are one place.

IDN-2, implementing §2.2(c). The shape of the rule it encodes:

  - The founding set is merged **by a person**, one pair at a time, in one
    sitting. `propose_duplicate_locations` finds candidates; nothing applies
    them without `confirm_merge`.
  - After that, exact-address matches resolve to the same dock with no merge at
    all - that is IDN-1's unique key doing the work - and only near-misses reach
    the queue.
  - Every merge, human or automatic, writes an audit row and can be reverted.

**Why a person confirms the founding set.** The founding state of the reference
table is the moat's initial condition and the failure mode is silent: a bad
merge is invisible afterwards, because the two docks have become one and nothing
downstream can see the seam. One afternoon of attention buys a verified starting
point. That asymmetry - a missed merge is visible and cheap, a wrong merge is
invisible and permanent - is why this module proposes and never decides.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.account_signals import (
    TIER_HIGH,
    TIER_WEAK,
    why_these_accounts_might_be_one_place,
)
from app.identity.resolution import canonical_location
from app.models.location import Location
from app.models.location_merge import (
    SOURCE_AUTOMATIC,
    SOURCE_HUMAN,
    STATUS_APPLIED,
    STATUS_PROPOSED,
    STATUS_REJECTED,
    STATUS_REVERTED,
    STATUS_SUPERSEDED,
    LocationMerge,
)
from app.models.shop import Shop

# Above this, two normalized addresses are similar enough to be worth a person's
# attention. Tuned to propose generously rather than precisely: the cost of a
# candidate that turns out to be two real places is ten seconds of review, and
# the cost of missing one is a dock that stays split and quietly halves its own
# stop count. Nothing here merges on its own, so a loose threshold is cheap.
SIMILARITY_THRESHOLD = 0.88

# Two docks this close together are the same building for delivery purposes.
# ~0.0003 degrees is roughly 30m at these latitudes - inside one car park, well
# outside next door.
COORDINATE_EPSILON = 0.0003


async def propose_duplicate_locations(
    session: AsyncSession, *, limit: int = 2000
) -> list[LocationMerge]:
    """Find dock pairs worth a person's review and queue them.

    Compares **accounts**, not addresses. The first version of this compared
    normalized addresses and coordinates, which turned out to be the wrong
    signal entirely: on the design partner's real export, address
    normalisation collapses 230 accounts to 229 and there are no coordinates at
    all. What does carry the signal is the distributor's own account id, the
    name, and the postcode - see `account_signals.py` for the rules and where
    they came from.

    Address similarity is kept as a last resort, for the case where two records
    share no account structure but plainly name the same street. It fires
    rarely and is tiered accordingly.

    Already-merged docks are skipped, and a pair that has been proposed,
    rejected or merged before is not proposed again: re-asking a question
    somebody already answered is how a review queue becomes noise that gets
    cleared without being read.
    """
    # One row per (shop, its dock). A dock reached by several accounts appears
    # several times, which is correct - the account is what carries the signal.
    rows = (
        await session.execute(
            select(Shop, Location)
            .join(Location, Shop.location_id == Location.id)
            .where(Location.merged_into_id.is_(None))
            .order_by(Shop.created_at, Shop.id)
            .limit(limit)
        )
    ).all()
    decided = await _already_considered(session)

    proposals: list[LocationMerge] = []
    for index, (shop_a, loc_a) in enumerate(rows):
        for shop_b, loc_b in rows[index + 1 :]:
            if loc_a.id == loc_b.id:
                continue  # already the same dock

            source, target = _order_pair(loc_a, loc_b)
            if (source.id, target.id) in decided:
                continue

            verdict = why_these_accounts_might_be_one_place(
                ref_a=shop_a.external_ref, name_a=shop_a.name, address_a=shop_a.address,
                ref_b=shop_b.external_ref, name_b=shop_b.name, address_b=shop_b.address,
            )
            if verdict is None:
                verdict = _why_these_addresses_might_match(source, target)
            if verdict is None:
                continue

            tier, reason = verdict
            proposal = LocationMerge(
                source_location_id=source.id,
                target_location_id=target.id,
                status=STATUS_PROPOSED,
                # Tier first, so a reviewer can work the confident ones before
                # the ambiguous ones. A flat queue gets cleared, not read.
                reason=f"{tier} - {reason}"[:255],
            )
            session.add(proposal)
            proposals.append(proposal)
            decided.add((source.id, target.id))

    if proposals:
        await session.flush()
    return proposals


def _why_these_addresses_might_match(
    source: Location, target: Location
) -> tuple[str, str] | None:
    """The original signals, demoted to a fallback.

    Coordinates are absent from the export we have, and address similarity
    barely fires on it - but neither will always be true. A geocoded book, or a
    customer who writes addresses consistently, makes these useful again, and
    deleting them would mean rediscovering them later.
    """
    if (
        source.geocoded
        and target.geocoded
        and abs(source.lat - target.lat) < COORDINATE_EPSILON
        and abs(source.lng - target.lng) < COORDINATE_EPSILON
    ):
        return TIER_HIGH, "same coordinates to within ~30m"

    ratio = SequenceMatcher(
        None, source.normalized_address, target.normalized_address
    ).ratio()
    if ratio >= SIMILARITY_THRESHOLD:
        return TIER_WEAK, f"addresses {ratio:.2f} similar"

    return None


def _order_pair(left: Location, right: Location) -> tuple[Location, Location]:
    """Newer row is absorbed into older. Returns `(source, target)`.

    Arbitrary but fixed. What matters is that it is stable across runs, so the
    same pair is never queued twice in opposite directions - which would let a
    reviewer approve both and build a cycle.
    """
    if (left.created_at, str(left.id)) <= (right.created_at, str(right.id)):
        return right, left
    return left, right


async def _already_considered(session: AsyncSession) -> set[tuple[UUID, UUID]]:
    """Every pair that has been proposed, merged, rejected or reverted before.

    A reverted merge counts: somebody merged these two and then undid it, and
    proposing it again as though that never happened is worse than silence.
    """
    rows = await session.execute(
        select(LocationMerge.source_location_id, LocationMerge.target_location_id)
    )
    pairs: set[tuple[UUID, UUID]] = set()
    for source_id, target_id in rows:
        pairs.add((source_id, target_id))
        # Both directions, so a pair proposed one way is not re-proposed the
        # other way if row creation order ever changes.
        pairs.add((target_id, source_id))
    return pairs


async def propose_merge(
    session: AsyncSession,
    *,
    source: Location,
    target: Location,
    reason: str,
) -> LocationMerge:
    """Queue one specific pair, for a reviewer who spotted it themselves."""
    _refuse_impossible_merge(source, target)
    proposal = LocationMerge(
        source_location_id=source.id,
        target_location_id=target.id,
        status=STATUS_PROPOSED,
        reason=reason,
    )
    session.add(proposal)
    await session.flush()
    return proposal


async def pending_merges(session: AsyncSession) -> list[LocationMerge]:
    """The review queue: everything proposed and not yet decided."""
    return list(
        await session.scalars(
            select(LocationMerge)
            .where(LocationMerge.status == STATUS_PROPOSED)
            .order_by(LocationMerge.created_at)
        )
    )


async def recently_applied_merges(
    session: AsyncSession, *, limit: int = 10
) -> list[LocationMerge]:
    """Merges that went through, newest first — the undo list.

    *"Every merge audited and reversible"* is a clause of `IDN-2`'s done-when,
    and `revert_merge` delivered the second half in code while nothing listed an
    applied merge for anybody to reverse. Reversible-by-curl is a thin reading
    of it.

    Capped and recent on purpose. Undo is for the merge somebody has just
    realised was wrong; a complete history of every merge ever made is an audit
    question, and answering both here would bury the one in the other.
    """
    return list(
        await session.scalars(
            select(LocationMerge)
            .where(LocationMerge.status == STATUS_APPLIED)
            .order_by(LocationMerge.decided_at.desc().nulls_last())
            .limit(limit)
        )
    )


async def confirm_merge(
    session: AsyncSession,
    proposal: LocationMerge,
    *,
    ops_user_id: UUID,
) -> LocationMerge:
    """A person says these two docks are one place. Applies the merge."""
    if proposal.status != STATUS_PROPOSED:
        raise ValueError(f"cannot confirm a merge that is {proposal.status}")
    return await _apply(
        session,
        proposal,
        decision_source=SOURCE_HUMAN,
        ops_user_id=ops_user_id,
    )


async def reject_merge(
    session: AsyncSession,
    proposal: LocationMerge,
    *,
    ops_user_id: UUID,
) -> LocationMerge:
    """A person says these are different places. Recorded, not deleted.

    The rejection is the useful artefact: it stops the pair being re-proposed
    every time the detector runs, and it is the evidence that somebody looked.
    """
    if proposal.status != STATUS_PROPOSED:
        raise ValueError(f"cannot reject a merge that is {proposal.status}")
    proposal.status = STATUS_REJECTED
    proposal.decision_source = SOURCE_HUMAN
    proposal.decided_by_ops_user_id = ops_user_id
    proposal.decided_at = datetime.now(timezone.utc)
    await session.flush()
    return proposal


async def merge_locations(
    session: AsyncSession,
    *,
    source: Location,
    target: Location,
    reason: str,
) -> LocationMerge:
    """Merge without review. The `automatic` path §2.2(c) allows after the founding set.

    Still writes the same audit row and is still reversible - "automatic"
    describes who decided, not whether it is recorded.
    """
    _refuse_impossible_merge(source, target)
    proposal = LocationMerge(
        source_location_id=source.id,
        target_location_id=target.id,
        status=STATUS_PROPOSED,
        reason=reason,
    )
    session.add(proposal)
    await session.flush()
    return await _apply(
        session, proposal, decision_source=SOURCE_AUTOMATIC, ops_user_id=None
    )


async def revert_merge(
    session: AsyncSession,
    merge: LocationMerge,
    *,
    ops_user_id: UUID,
) -> LocationMerge:
    """Undo an applied merge, putting back exactly the shops it moved.

    `moved_shop_ids` is why this is reversible rather than approximately
    reversible: shops that already pointed at the target before the merge are
    not touched, because they were never moved.
    """
    if merge.status != STATUS_APPLIED:
        raise ValueError(f"cannot revert a merge that is {merge.status}")

    source = await session.get(Location, merge.source_location_id)
    if source is None:
        raise ValueError("the merged dock no longer exists")

    source.merged_into_id = None
    for shop_id in merge.moved_shop_ids or []:
        shop = await session.get(Shop, UUID(str(shop_id)))
        if shop is not None:
            shop.location_id = source.id

    merge.status = STATUS_REVERTED
    merge.reverted_at = datetime.now(timezone.utc)
    # Deliberately not overwriting `decided_by_ops_user_id`: that field records
    # who authorised the merge, and a revert must not erase it. Who reverted is
    # recoverable from the row's `updated_at` and the ops audit trail; who
    # merged is not recoverable from anywhere else.
    await session.flush()
    return merge


def _refuse_impossible_merge(source: Location, target: Location) -> None:
    if source.id == target.id:
        raise ValueError("a dock cannot be merged into itself")
    if source.merged_into_id is not None:
        raise ValueError("the source dock is already merged into another")


async def _apply(
    session: AsyncSession,
    proposal: LocationMerge,
    *,
    decision_source: str,
    ops_user_id: UUID | None,
) -> LocationMerge:
    raw_source = await session.get(Location, proposal.source_location_id)
    raw_target = await session.get(Location, proposal.target_location_id)
    if raw_source is None or raw_target is None:
        raise ValueError("a dock in this proposal no longer exists")

    # Resolve BOTH sides through their alias chains before doing anything. A
    # proposal can sit in the queue while either dock is merged elsewhere, and
    # the decision it records - "these two are the same place" - is about the
    # places, not about the rows that represented them when it was queued.
    source = await canonical_location(session, raw_source)
    target = await canonical_location(session, raw_target)

    if source.id == target.id:
        # The reviewer is right and there is nothing left to do: an earlier
        # merge already brought these together. Recorded as a real decision,
        # because it is one - and kept out of `applied` so a later revert
        # cannot undo a merge this proposal did not make.
        proposal.status = STATUS_SUPERSEDED
        proposal.decision_source = decision_source
        proposal.decided_by_ops_user_id = ops_user_id
        proposal.decided_at = datetime.now(timezone.utc)
        await session.flush()
        return proposal

    moved = list(
        await session.scalars(select(Shop).where(Shop.location_id == source.id))
    )
    for shop in moved:
        shop.location_id = target.id

    source.merged_into_id = target.id

    # Both sides are written back, not just the target. `_apply` may have
    # resolved either through an alias chain, and a row that names a dock the
    # merge did not touch makes `revert_merge` undo the wrong merge.
    proposal.source_location_id = source.id
    proposal.target_location_id = target.id
    proposal.status = STATUS_APPLIED
    proposal.decision_source = decision_source
    proposal.decided_by_ops_user_id = ops_user_id
    proposal.decided_at = datetime.now(timezone.utc)
    proposal.moved_shop_ids = [str(shop.id) for shop in moved]

    await session.flush()
    return proposal


@dataclass(frozen=True)
class MergeScale:
    """How much each side of a proposed merge already holds.

    **The number a reviewer was never shown.** `confirm_merge` applies
    immediately and the queue described one pair, so a person answering *"are
    these two the same place"* had no way to see that the dock on the right had
    already absorbed four others. Confirm twenty defensible pairs in a sitting
    and the twentieth joins two groups that were never compared - which is
    exactly what `AGT-1` found when it closed this resolver's proposals
    transitively: a dock holding a municipal DPW, two county departments and an
    unrelated business, every edge in the chain individually fine.

    A merge is the one operation here that cannot be seen after the fact, since
    erasing the seam is what it is for. So the seam has to be shown before.
    """

    source_shops: int
    target_shops: int
    source_absorbed: int
    target_absorbed: int

    @property
    def accounts_joined(self) -> int:
        """Docks that would share one identity if this were confirmed."""
        return self.source_absorbed + self.target_absorbed + 2

    @property
    def is_a_chain(self) -> bool:
        """Whether either side is already a group rather than a dock.

        Two virgin docks merging is the ordinary case and needs no warning.
        The moment one side has absorbed something, confirming extends a chain
        the reviewer did not build and cannot see.
        """
        return bool(self.source_absorbed or self.target_absorbed)


async def merge_scale(session: AsyncSession, proposal: LocationMerge) -> MergeScale:
    """What confirming this proposal would actually join together."""
    applied = select(LocationMerge).where(LocationMerge.status == STATUS_APPLIED)

    async def absorbed(location_id: UUID) -> int:
        rows = await session.scalars(
            applied.where(LocationMerge.target_location_id == location_id)
        )
        return len(list(rows))

    async def shops(location_id: UUID) -> int:
        rows = await session.scalars(
            select(Shop.id).where(Shop.location_id == location_id)
        )
        return len(list(rows))

    return MergeScale(
        source_shops=await shops(proposal.source_location_id),
        target_shops=await shops(proposal.target_location_id),
        source_absorbed=await absorbed(proposal.source_location_id),
        target_absorbed=await absorbed(proposal.target_location_id),
    )
