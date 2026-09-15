"""IDN-2: the alias map, the review queue, and merges that can be undone.

`docs/ROADMAP_1.5.md` Phase 1, §2.2(c). The done-when is "the five duplicate
body-shop records collapse to one dock, and the merge is in the audit log", so
that is the first test here.

The rule being enforced throughout: this module **proposes and never decides**.
A missed merge is visible and cheap - the dock stays split and somebody notices
the stop count. A wrong merge is invisible and permanent - two places have
become one and no downstream check can see the seam. Every test below is
downstream of that asymmetry.
"""
import uuid

import pytest
from sqlalchemy import select

from app.identity import (
    canonical_location,
    confirm_merge,
    merge_locations,
    pending_merges,
    propose_duplicate_locations,
    propose_merge,
    reject_merge,
    resolve_location,
    revert_merge,
)
from app.models.client import Client
from app.models.hub import Hub
from app.models.location import Location
from app.models.location_merge import (
    SOURCE_AUTOMATIC,
    SOURCE_HUMAN,
    STATUS_APPLIED,
    STATUS_REJECTED,
    STATUS_REVERTED,
    STATUS_SUPERSEDED,
    LocationMerge,
)
from app.models.ops_user import OpsUser
from app.models.shop import Shop

pytestmark = pytest.mark.integration

# One body shop, five records, two ID roots - the real case from the design
# partner's export. The addresses differ; exact-key matching (IDN-1) cannot
# collapse them, which is the entire reason IDN-2 exists.
BODY_SHOP_RECORDS = [
    "4400 Industrial Blvd, Austin, TX",
    "4400 Industrial Boulevard, Austin, TX",
    "4400 Industrial Blvd Ste B, Austin, TX",
    "4400 Industrial Blvd., Austin, Texas",
    "4400 Industral Blvd, Austin, TX",
]
BODY_SHOP_COORDS = (30.2501, -97.7401)


async def _seed_ops_user(db_session) -> uuid.UUID:
    ops_id = uuid.uuid4()
    db_session.add(
        OpsUser(
            id=ops_id,
            email=f"ops-{ops_id.hex[:6]}@lmxit.com",
            password_hash="x",
            name="Identity Reviewer",
            role="admin",
        )
    )
    await db_session.flush()
    return ops_id


async def _seed_client(db_session) -> uuid.UUID:
    hub = Hub(id=uuid.uuid4(), name="Merge Test Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Merge Test Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    return client.id


async def _seed_body_shop(db_session) -> tuple[list[Location], list[Shop]]:
    """Five shop records at one physical dock, each resolving to its own Location."""
    client_id = await _seed_client(db_session)
    lat, lng = BODY_SHOP_COORDS

    locations, shops = [], []
    for index, address in enumerate(BODY_SHOP_RECORDS):
        location = await resolve_location(db_session, address=address, lat=lat, lng=lng)
        shop = Shop(
            client_id=client_id,
            name=f"Body Shop record {index}",
            address=address,
            lat=lat,
            lng=lng,
            location_id=location.id,
        )
        db_session.add(shop)
        locations.append(location)
        shops.append(shop)
    await db_session.flush()
    return locations, shops


async def test_five_body_shop_records_collapse_to_one_dock_with_an_audit_trail(db_session):
    """IDN-2's done-when, exactly as the roadmap words it."""
    ops_user_id = await _seed_ops_user(db_session)
    locations, shops = await _seed_body_shop(db_session)

    assert len({location.id for location in locations}) == 5, (
        "precondition: five records start as five separate docks"
    )

    # A person opens the queue and confirms what the detector proposed.
    proposed = await propose_duplicate_locations(db_session)
    assert proposed, "the detector found nothing to review"

    for proposal in await pending_merges(db_session):
        await confirm_merge(db_session, proposal, ops_user_id=ops_user_id)

    # Every one of the five shops now points at the same dock.
    docks = set()
    for shop in shops:
        await db_session.refresh(shop)
        docks.add(shop.location_id)
    assert len(docks) == 1, f"expected one dock, got {len(docks)}"

    # And every address still resolves there, because the absorbed rows survive
    # as aliases rather than being deleted.
    canonical_id = next(iter(docks))
    for address in BODY_SHOP_RECORDS:
        resolved = await resolve_location(db_session, address=address)
        assert resolved.id == canonical_id

    # The audit log records who did it and why.
    applied = list(
        await db_session.scalars(
            select(LocationMerge).where(LocationMerge.status == STATUS_APPLIED)
        )
    )
    assert len(applied) == 4, "collapsing five docks takes four merges"
    for merge in applied:
        assert merge.decision_source == SOURCE_HUMAN
        assert merge.decided_by_ops_user_id == ops_user_id
        assert merge.decided_at is not None
        assert merge.reason, "a reviewer has to be told why the pair was proposed"


async def test_a_merged_address_resolves_to_the_canonical_dock(db_session):
    """The alias map. The absorbed row keeps its key and forwards.

    This is what stops the next import recreating the duplicate, which is how
    de-duplication efforts usually fail.
    """
    ops_user_id = await _seed_ops_user(db_session)
    keep = await resolve_location(db_session, address="1 Real St, Austin, TX")
    alias = await resolve_location(db_session, address="1 Real Street, Austin, TX")

    proposal = await propose_merge(
        db_session, source=alias, target=keep, reason="test"
    )
    await confirm_merge(db_session, proposal, ops_user_id=ops_user_id)

    again = await resolve_location(db_session, address="1 Real Street, Austin, TX")
    assert again.id == keep.id
    assert again.merged_into_id is None

    await db_session.refresh(alias)
    assert alias.merged_into_id == keep.id, "the absorbed row is kept as an alias"


async def test_revert_puts_back_only_the_shops_the_merge_moved(db_session):
    """Reversible has to mean exactly reversible, or it is a claim not a fact."""
    ops_user_id = await _seed_ops_user(db_session)
    client_id = await _seed_client(db_session)

    keep = await resolve_location(db_session, address="1 Real St, Austin, TX")
    alias = await resolve_location(db_session, address="1 Real Street, Austin, TX")

    native = Shop(
        client_id=client_id, name="Already here", address="1 Real St, Austin, TX",
        lat=30.0, lng=-97.0, location_id=keep.id,
    )
    moving = Shop(
        client_id=client_id, name="Will move", address="1 Real Street, Austin, TX",
        lat=30.0, lng=-97.0, location_id=alias.id,
    )
    db_session.add_all([native, moving])
    await db_session.flush()

    proposal = await propose_merge(db_session, source=alias, target=keep, reason="test")
    merge = await confirm_merge(db_session, proposal, ops_user_id=ops_user_id)

    await db_session.refresh(moving)
    assert moving.location_id == keep.id
    assert merge.moved_shop_ids == [str(moving.id)]

    await revert_merge(db_session, merge, ops_user_id=ops_user_id)

    await db_session.refresh(moving)
    await db_session.refresh(native)
    await db_session.refresh(alias)
    assert moving.location_id == alias.id, "the moved shop went back"
    assert native.location_id == keep.id, "the shop that never moved was not touched"
    assert alias.merged_into_id is None, "the alias is a dock again"
    assert merge.status == STATUS_REVERTED
    assert merge.reverted_at is not None
    assert merge.decided_by_ops_user_id == ops_user_id, (
        "a revert must not erase who authorised the merge"
    )


async def test_a_rejected_pair_is_recorded_and_not_proposed_again(db_session):
    """The rejection is the artefact: evidence somebody looked, and queue hygiene."""
    ops_user_id = await _seed_ops_user(db_session)
    await _seed_body_shop(db_session)

    first_round = await propose_duplicate_locations(db_session)
    assert first_round

    for proposal in await pending_merges(db_session):
        await reject_merge(db_session, proposal, ops_user_id=ops_user_id)

    second_round = await propose_duplicate_locations(db_session)
    assert second_round == [], "a pair somebody already answered was re-proposed"

    rejected = list(
        await db_session.scalars(
            select(LocationMerge).where(LocationMerge.status == STATUS_REJECTED)
        )
    )
    assert rejected
    for merge in rejected:
        assert merge.decided_by_ops_user_id == ops_user_id
        assert merge.decided_at is not None


async def test_nothing_is_merged_without_a_decision(db_session):
    """The detector proposes. It must never apply."""
    await _seed_body_shop(db_session)

    await propose_duplicate_locations(db_session)

    merged = list(
        await db_session.scalars(
            select(Location).where(Location.merged_into_id.is_not(None))
        )
    )
    assert merged == [], "the detector applied a merge on its own"
    applied = list(
        await db_session.scalars(
            select(LocationMerge).where(LocationMerge.status == STATUS_APPLIED)
        )
    )
    assert applied == []


async def test_an_automatic_merge_is_still_audited_and_reversible(db_session):
    """§2.2(c)'s post-founding path. "Automatic" describes who decided, not whether
    it is recorded."""
    ops_user_id = await _seed_ops_user(db_session)
    keep = await resolve_location(db_session, address="1 Real St, Austin, TX")
    alias = await resolve_location(db_session, address="1 Real Street, Austin, TX")

    merge = await merge_locations(
        db_session, source=alias, target=keep, reason="exact normalized match"
    )

    assert merge.status == STATUS_APPLIED
    assert merge.decision_source == SOURCE_AUTOMATIC
    assert merge.decided_by_ops_user_id is None
    assert merge.decided_at is not None

    await revert_merge(db_session, merge, ops_user_id=ops_user_id)
    await db_session.refresh(alias)
    assert alias.merged_into_id is None


async def test_confirming_a_stale_proposal_lands_on_the_current_dock(db_session):
    """A proposal can sit in the queue while its target is merged elsewhere.

    Confirming it must land on the dock that exists now, not build a two-hop
    chain that every reader then has to walk.
    """
    ops_user_id = await _seed_ops_user(db_session)
    a = await resolve_location(db_session, address="1 Real St, Austin, TX")
    b = await resolve_location(db_session, address="1 Real Street, Austin, TX")
    c = await resolve_location(db_session, address="1 Real Str, Austin, TX")

    stale = await propose_merge(db_session, source=c, target=b, reason="queued first")
    # b is merged into a while that proposal waits.
    b_into_a = await propose_merge(db_session, source=b, target=a, reason="decided first")
    await confirm_merge(db_session, b_into_a, ops_user_id=ops_user_id)

    applied = await confirm_merge(db_session, stale, ops_user_id=ops_user_id)

    await db_session.refresh(c)
    assert c.merged_into_id == a.id, "should point at the surviving dock, not at b"
    assert applied.target_location_id == a.id, "the audit row records where it landed"


async def test_a_dock_cannot_be_merged_into_itself_or_merged_twice(db_session):
    ops_user_id = await _seed_ops_user(db_session)
    keep = await resolve_location(db_session, address="1 Real St, Austin, TX")
    alias = await resolve_location(db_session, address="1 Real Street, Austin, TX")

    with pytest.raises(ValueError):
        await propose_merge(db_session, source=keep, target=keep, reason="self")

    proposal = await propose_merge(db_session, source=alias, target=keep, reason="test")
    await confirm_merge(db_session, proposal, ops_user_id=ops_user_id)

    with pytest.raises(ValueError):
        await propose_merge(db_session, source=alias, target=keep, reason="again")


async def test_an_alias_cycle_is_refused_rather_than_looped_over(db_session):
    """No code path here builds one, which is why it is worth proving loudly.

    A cycle reached by manual surgery on the column would otherwise hang every
    request that touches the dock.
    """
    a = await resolve_location(db_session, address="1 Real St, Austin, TX")
    b = await resolve_location(db_session, address="1 Real Street, Austin, TX")

    a.merged_into_id = b.id
    b.merged_into_id = a.id
    await db_session.flush()

    with pytest.raises(RuntimeError, match="cycle"):
        await canonical_location(db_session, a)


async def test_a_superseded_confirmation_cannot_be_reverted(db_session):
    """The reason `superseded` is a distinct status rather than `applied`.

    Collapsing five records takes four merges, but a detector comparing every
    pair queues ten proposals. The other six are true statements that change
    nothing - and if they were recorded as `applied`, reverting one would clear
    an alias it never set, silently splitting a dock back apart.
    """
    ops_user_id = await _seed_ops_user(db_session)
    await _seed_body_shop(db_session)

    await propose_duplicate_locations(db_session)
    for proposal in await pending_merges(db_session):
        await confirm_merge(db_session, proposal, ops_user_id=ops_user_id)

    superseded = list(
        await db_session.scalars(
            select(LocationMerge).where(LocationMerge.status == STATUS_SUPERSEDED)
        )
    )
    assert superseded, "ten proposals over five docks must leave some with nothing to do"

    for merge in superseded:
        # Still a recorded decision - a person did look at it and say yes.
        assert merge.decision_source == SOURCE_HUMAN
        assert merge.decided_by_ops_user_id == ops_user_id
        assert merge.moved_shop_ids is None
        with pytest.raises(ValueError, match="superseded"):
            await revert_merge(db_session, merge, ops_user_id=ops_user_id)

    # And the docks stayed collapsed throughout.
    unmerged = list(
        await db_session.scalars(
            select(Location).where(Location.merged_into_id.is_(None))
        )
    )
    assert len(unmerged) == 1
