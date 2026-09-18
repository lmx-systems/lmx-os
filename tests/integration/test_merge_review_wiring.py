"""IDN-2: the merge review queue has a producer and a reviewer.

The audit's last finding. *"Human-confirms the founding ~230, auto-merge after,
every merge audited and reversible"* — and nothing called `propose_merge` or
`propose_duplicate_locations` outside a one-off script, so the queue a human
reviews was permanently empty. The founding set could not be confirmed because
there was nothing to confirm.

Wiring it needed `IDN-1` first, and that only landed two changes ago: until
shops were linked to docks at creation, there were no near-duplicate `Location`
rows for a detector to find.

`propose_duplicate_locations` runs once a night across all hubs — not per hub.
The same physical dock can be reached from two hubs, and scoping the comparison
would make exactly that pair invisible, which is the most valuable merge to
catch.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.identity.merge import propose_duplicate_locations
from app.models.client import Client
from app.models.hub import Hub
from app.models.location import Location
from app.models.location_merge import (
    STATUS_APPLIED,
    STATUS_PROPOSED,
    STATUS_REJECTED,
    LocationMerge,
)
from app.models.ops_user import ADMIN_ROLE, VIEWER_ROLE, OpsUser
from app.models.shop import Shop
from app.ops_auth.dependencies import AuthedOpsUser

pytestmark = pytest.mark.integration

VIEWER = AuthedOpsUser(
    ops_user_id=str(uuid.uuid4()), email="v@example.com", name="Viewer", role=VIEWER_ROLE
)


async def _admin(db_session) -> AuthedOpsUser:
    """A real `ops_users` row, because `location_merges.decided_by_ops_user_id`
    is a foreign key - a merge has to be attributable to somebody who exists,
    which is the point of recording who decided it."""
    user = OpsUser(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        name="Admin",
        role=ADMIN_ROLE,
    )
    db_session.add(user)
    await db_session.flush()
    return AuthedOpsUser(
        ops_user_id=str(user.id), email=user.email, name=user.name, role=ADMIN_ROLE
    )


async def _two_docks(db_session, *, same_account=True) -> tuple[Location, Location]:
    """Two docks a detector should pair, reached by two shop records.

    Built the way intake now builds them - a shop, then its dock - rather than
    by inserting `Location` rows directly, because `IDN-1`'s link is what makes
    these exist at all.
    """
    hub = Hub(id=uuid.uuid4(), name="Merge Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()

    docks = []
    suffix = "" if same_account else "-OTHER"
    for i, address in enumerate(("100 Trade St Unit A", "100 Trade St Unit B")):
        dock = Location(
            normalized_address=f"100tradestunit{'ab'[i]}",
            address=address,
            lat=30.26,
            lng=-97.74,
        )
        db_session.add(dock)
        await db_session.flush()
        db_session.add(
            Shop(
                client_id=client.id,
                name=f"Trade Street {i}",
                address=address,
                lat=30.26,
                lng=-97.74,
                external_ref=f"ACCT-4471{suffix if i else ''}",
                location_id=dock.id,
            )
        )
        docks.append(dock)
    await db_session.flush()
    return docks[0], docks[1]


class TestTheQueueHasAProducer:
    async def test_the_detector_proposes_a_pair(self, db_session):
        """The finding, in one assertion. Before this the queue was empty every
        night, forever, and the founding ~230 could not be confirmed."""
        await _two_docks(db_session)

        proposals = await propose_duplicate_locations(db_session)

        assert len(proposals) >= 1
        assert all(p.status == STATUS_PROPOSED for p in proposals)
        assert all(p.reason for p in proposals), "a proposal with no reason cannot be judged"

    async def test_a_pair_already_answered_is_not_re_proposed(self, db_session):
        """A queue that re-asks a question somebody already answered becomes
        noise that gets cleared without being read."""
        await _two_docks(db_session)
        first = await propose_duplicate_locations(db_session)
        assert first
        for proposal in first:
            proposal.status = STATUS_REJECTED
            proposal.decided_at = datetime.now(timezone.utc)
        await db_session.flush()

        second = await propose_duplicate_locations(db_session)

        assert second == []

    async def test_the_nightly_tick_claims_it_once_across_all_hubs(self):
        """Not once per hub. The comparison is global on purpose - the same
        physical dock can be reached from two hubs - so running it per hub would
        do the same global work N times and find nothing extra."""
        import inspect

        from app.learning_loop import scheduler

        source = inspect.getsource(scheduler)
        assert "propose_duplicate_locations(session)" in source
        assert "_global_job_key" in source, "a global job needs a once-a-day claim"


class TestTheQueueHasAReviewer:
    async def test_the_endpoint_shows_both_addresses(self, db_session):
        """"Are these the same place" cannot be answered from two UUIDs, and a
        reviewer who has to look each one up will not."""
        from app.api.routes import merge_proposals

        await _two_docks(db_session)
        await propose_duplicate_locations(db_session)
        await db_session.flush()

        view = await merge_proposals(session=db_session, _ops=VIEWER)

        assert len(view) >= 1
        assert "100 Trade St Unit A" in {v.source_address for v in view} | {
            v.target_address for v in view
        }
        assert view[0].reason

    async def test_confirming_applies_the_merge(self, db_session):
        from app.api.routes import confirm_merge_proposal, merge_proposals
        admin = await _admin(db_session)

        await _two_docks(db_session)
        await propose_duplicate_locations(db_session)
        await db_session.flush()
        proposal = (await merge_proposals(session=db_session, _ops=VIEWER))[0]

        result = await confirm_merge_proposal(
            proposal_id=proposal.id, session=db_session, admin=admin
        )

        assert result.status == STATUS_APPLIED
        merged = await db_session.get(Location, proposal.source_location_id)
        assert merged.merged_into_id == proposal.target_location_id

    async def test_rejecting_records_the_decision_rather_than_deleting(self, db_session):
        """The rejection is the useful artefact - it is the evidence somebody
        looked, and it is what stops the pair coming back tomorrow."""
        from app.api.routes import merge_proposals, reject_merge_proposal
        admin = await _admin(db_session)

        await _two_docks(db_session)
        await propose_duplicate_locations(db_session)
        await db_session.flush()
        proposal = (await merge_proposals(session=db_session, _ops=VIEWER))[0]

        result = await reject_merge_proposal(
            proposal_id=proposal.id, session=db_session, admin=admin
        )

        assert result.status == STATUS_REJECTED
        assert await db_session.scalar(
            select(func.count()).select_from(LocationMerge)
        ) >= 1, "recorded, not deleted"

    async def test_a_decided_proposal_leaves_the_queue(self, db_session):
        from app.api.routes import merge_proposals, reject_merge_proposal
        admin = await _admin(db_session)

        await _two_docks(db_session)
        await propose_duplicate_locations(db_session)
        await db_session.flush()
        for proposal in await merge_proposals(session=db_session, _ops=VIEWER):
            await reject_merge_proposal(
                proposal_id=proposal.id, session=db_session, admin=admin
            )

        assert await merge_proposals(session=db_session, _ops=VIEWER) == []

    async def test_deciding_twice_is_refused_readably(self, db_session):
        from fastapi import HTTPException
        admin = await _admin(db_session)

        from app.api.routes import confirm_merge_proposal, merge_proposals

        await _two_docks(db_session)
        await propose_duplicate_locations(db_session)
        await db_session.flush()
        proposal = (await merge_proposals(session=db_session, _ops=VIEWER))[0]
        await confirm_merge_proposal(proposal_id=proposal.id, session=db_session, admin=admin)

        with pytest.raises(HTTPException) as exc:
            await confirm_merge_proposal(
                proposal_id=proposal.id, session=db_session, admin=admin
            )
        assert exc.value.status_code == 409

    async def test_an_unknown_proposal_is_a_404(self, db_session):
        from fastapi import HTTPException
        admin = await _admin(db_session)

        from app.api.routes import confirm_merge_proposal

        with pytest.raises(HTTPException) as exc:
            await confirm_merge_proposal(
                proposal_id=uuid.uuid4(), session=db_session, admin=admin
            )
        assert exc.value.status_code == 404


class TestEveryMergeIsReversible:
    async def test_reverting_puts_the_shops_back(self, db_session):
        """A clause of the done-when, reversible in code with nothing able to
        call it until now."""
        from app.api.routes import confirm_merge_proposal, merge_proposals, revert_applied_merge
        admin = await _admin(db_session)

        source, target = await _two_docks(db_session)
        await propose_duplicate_locations(db_session)
        await db_session.flush()
        proposal = (await merge_proposals(session=db_session, _ops=VIEWER))[0]
        applied = await confirm_merge_proposal(
            proposal_id=proposal.id, session=db_session, admin=admin
        )

        result = await revert_applied_merge(
            merge_id=applied.id, session=db_session, admin=admin
        )

        assert result.status != STATUS_APPLIED
        restored = await db_session.get(Location, applied.source_location_id)
        assert restored.merged_into_id is None

    async def test_reverting_something_not_applied_is_refused(self, db_session):
        from fastapi import HTTPException
        admin = await _admin(db_session)

        from app.api.routes import merge_proposals, revert_applied_merge

        await _two_docks(db_session)
        await propose_duplicate_locations(db_session)
        await db_session.flush()
        proposal = (await merge_proposals(session=db_session, _ops=VIEWER))[0]

        with pytest.raises(HTTPException) as exc:
            await revert_applied_merge(
                merge_id=proposal.id, session=db_session, admin=admin
            )
        assert exc.value.status_code == 409


class TestWhoMayDecide:
    async def test_reading_the_queue_is_open_to_any_ops_session(self):
        """A dispatcher spotting a duplicate is how good proposals get
        noticed."""
        import inspect

        from app.api.routes import merge_proposals
        from app.ops_auth.dependencies import get_current_ops_user

        dependency = inspect.signature(merge_proposals).parameters["_ops"].default
        assert dependency.dependency is get_current_ops_user

    @pytest.mark.parametrize(
        "name", ["confirm_merge_proposal", "reject_merge_proposal", "revert_applied_merge"]
    )
    async def test_deciding_needs_an_admin(self, name):
        """Confirming rewrites which dock a shop points at, and every per-dock
        statistic - dwell, node class, the receiver profile - moves with it.
        That is the shape `require_admin`'s docstring describes."""
        import inspect

        from app import api
        from app.ops_auth.dependencies import require_admin

        endpoint = getattr(api.routes, name)
        dependency = inspect.signature(endpoint).parameters["admin"].default
        assert dependency.dependency is require_admin
