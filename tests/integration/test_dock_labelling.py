"""IDN-3: the rules are exhausted, so a person labels the tail.

*"All ~230 accounts classified; unlabelled below 2%."* Measured against the
design partner's real account book the naming rules leave **28.7% unlabelled**,
and what remains is family and personal business names carrying no industry word
at all - the export has no industry code, so there is nothing else to read.

The row has always said the choice is *"a person labels the tail, or the target
moves"*. Nothing made the first possible: `set_node_class` existed and was called
by nothing (`docs/ROADMAP_AUDIT_2026-09.md`'s allowlist). This is that surface.

The ordering is the part worth testing. The tail is long, an arbitrary order gets
worked from the top until somebody stops, and so the order decides which docks
get labelled at all.
"""
import uuid

import pytest
from sqlalchemy import select

from app.identity.node_class import NODE_CLASSES, infer_node_class
from app.models.client import Client
from app.models.hub import Hub
from app.models.location import Location
from app.models.ops_user import VIEWER_ROLE
from app.models.receiver_profile import ReceiverProfile
from app.models.shop import Shop
from app.ops_auth.dependencies import AuthedOpsUser
from app.schemas.reporting import NodeClassRequest

pytestmark = pytest.mark.integration

OPS = AuthedOpsUser(
    ops_user_id="u1", email="dispatcher@lmxit.com", name="Dispatcher", role=VIEWER_ROLE
)


async def _dock(db_session, *, name: str, samples: int | None = None) -> Location:
    hub = await db_session.scalar(select(Hub).limit(1))
    if hub is None:
        hub = Hub(id=uuid.uuid4(), name="Label Hub", lat=30.27, lng=-97.74)
        db_session.add(hub)
        await db_session.flush()
    client = await db_session.scalar(select(Client).limit(1))
    if client is None:
        client = Client(
            id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file"
        )
        db_session.add(client)
        await db_session.flush()

    dock = Location(
        normalized_address=uuid.uuid4().hex, address=f"{name}, Austin, 78701",
        lat=30.26, lng=-97.74,
    )
    db_session.add(dock)
    await db_session.flush()
    db_session.add(
        Shop(
            client_id=client.id, name=name, address=dock.address, lat=30.26,
            lng=-97.74, external_ref=f"REF-{uuid.uuid4().hex[:6]}", location_id=dock.id,
        )
    )
    if samples is not None:
        db_session.add(
            ReceiverProfile(
                location_id=dock.id,
                inherited_dwell_p50_seconds=120,
                inherited_dwell_sample_count=samples,
                inherited_dwell_source="a previous operator",
            )
        )
    await db_session.flush()
    return dock


class TestTheRulesGotBetterButNotEnough:
    @pytest.mark.parametrize(
        "name,expected",
        [
            # Found by measuring the unlabelled tail. `\bauto\b` does not match
            # `automotive` - different tokens - so a bare "X Automotive" was
            # falling through entirely.
            ("Riverside Automotive", "shop"),
            # A fuel brand on a receiver taking deliveries of parts is a service
            # station doing repairs, not a filling pump.
            ("Exxon Service Station", "shop"),
            ("Hilltop Car Care", "shop"),
        ],
    )
    async def test_the_added_patterns_catch_what_they_were_added_for(self, name, expected):
        result = infer_node_class(name)
        assert result is not None, f"{name} should now classify"
        assert result[0] == expected

    async def test_a_more_specific_class_still_wins(self):
        """`automotive group` is a dealer and is ordered first. Adding a broad
        pattern to the last rule must not steal from an earlier one."""
        assert infer_node_class("Northside Automotive Group")[0] == "dealer"
        assert infer_node_class("Precision Auto Body")[0] == "body_shop"

    async def test_a_name_with_no_industry_word_still_does_not_classify(self):
        """The finding that makes this surface necessary rather than optional.
        Most of the remaining tail is a family name and a legal suffix, and no
        rule reads that."""
        assert infer_node_class("Campoli and Sons LLC") is None
        assert infer_node_class("Arturo Lorenzo Inc") is None


class TestAPersonCanLabelIt:
    async def test_labelling_a_dock_records_a_human_decision(self, db_session):
        from app.api.routes import label_dock

        dock = await _dock(db_session, name="Campoli and Sons")
        assert dock.node_class is None

        await label_dock(
            location_id=dock.id,
            body=NodeClassRequest(node_class="body_shop"),
            session=db_session,
            _ops=OPS,
        )

        await db_session.refresh(dock)
        assert dock.node_class == "body_shop"
        assert dock.node_class_source == "human"

    async def test_an_eighth_class_is_refused(self, db_session):
        """The seven are what PRD-1 groups by. An eighth appearing would split a
        group without anyone noticing, so this raises rather than storing it."""
        from fastapi import HTTPException

        from app.api.routes import label_dock

        dock = await _dock(db_session, name="Somewhere")

        with pytest.raises(HTTPException) as exc:
            await label_dock(
                location_id=dock.id,
                body=NodeClassRequest(node_class="gas_station"),
                session=db_session,
                _ops=OPS,
            )
        assert exc.value.status_code == 422

    async def test_every_class_the_console_offers_is_accepted(self, db_session):
        """A drift between the buttons and the vocabulary is invisible until a
        dispatcher picks the one that is refused."""
        from app.api.routes import label_dock

        for node_class in NODE_CLASSES:
            dock = await _dock(db_session, name=f"Place {node_class}")
            await label_dock(
                location_id=dock.id,
                body=NodeClassRequest(node_class=node_class),
                session=db_session,
                _ops=OPS,
            )
            await db_session.refresh(dock)
            assert dock.node_class == node_class

    async def test_an_unknown_dock_is_a_404(self, db_session):
        from fastapi import HTTPException

        from app.api.routes import label_dock

        with pytest.raises(HTTPException) as exc:
            await label_dock(
                location_id=uuid.uuid4(),
                body=NodeClassRequest(node_class="shop"),
                session=db_session,
                _ops=OPS,
            )
        assert exc.value.status_code == 404

    async def test_any_ops_session_may_label(self):
        """A dispatcher who has been to the door knows better than a regex over
        the account name. Making this admin-only would put the knowledge and the
        permission in different people."""
        import inspect

        from app.api.routes import label_dock
        from app.ops_auth.dependencies import get_current_ops_user

        dependency = inspect.signature(label_dock).parameters["_ops"].default
        assert dependency.dependency is get_current_ops_user


class TestTheOrderDecidesWhatGetsLabelled:
    async def test_the_busiest_dock_comes_first(self, db_session):
        """The tail is long and gets worked from the top until somebody stops,
        so ordering by evidence of volume is what makes the effort count."""
        from app.api.routes import unlabelled_docks

        quiet = await _dock(db_session, name="Quiet place", samples=2)
        busy = await _dock(db_session, name="Busy place", samples=80)
        await db_session.flush()

        view = await unlabelled_docks(limit=50, session=db_session, _ops=OPS)

        ids = [v.location_id for v in view]
        assert ids.index(busy.id) < ids.index(quiet.id)

    async def test_a_dock_with_no_history_is_listed_last_not_dropped(self, db_session):
        """Unknown volume is not zero volume. Dropping these would hide every
        dock we have never delivered to - which is most of the ones a new
        customer brings."""
        from app.api.routes import unlabelled_docks

        known = await _dock(db_session, name="Known place", samples=5)
        unknown = await _dock(db_session, name="Unknown place")
        await db_session.flush()

        view = await unlabelled_docks(limit=50, session=db_session, _ops=OPS)

        ids = [v.location_id for v in view]
        assert ids.index(known.id) < ids.index(unknown.id)
        assert unknown.id in ids

    async def test_a_labelled_dock_leaves_the_list(self, db_session):
        from app.api.routes import label_dock, unlabelled_docks

        dock = await _dock(db_session, name="Campoli and Sons")
        await db_session.flush()
        assert dock.id in {
            v.location_id
            for v in await unlabelled_docks(limit=50, session=db_session, _ops=OPS)
        }

        await label_dock(
            location_id=dock.id,
            body=NodeClassRequest(node_class="shop"),
            session=db_session,
            _ops=OPS,
        )

        assert dock.id not in {
            v.location_id
            for v in await unlabelled_docks(limit=50, session=db_session, _ops=OPS)
        }

    async def test_a_merged_dock_is_not_offered(self, db_session):
        """It is no longer a place - IDN-2 collapsed it into another. Labelling
        it would put a class on a row nothing reads."""
        from app.api.routes import unlabelled_docks

        target = await _dock(db_session, name="Target place")
        merged = await _dock(db_session, name="Merged place")
        merged.merged_into_id = target.id
        await db_session.flush()

        ids = {
            v.location_id
            for v in await unlabelled_docks(limit=50, session=db_session, _ops=OPS)
        }
        assert merged.id not in ids
        assert target.id in ids

    async def test_the_dock_carries_what_a_person_needs_to_judge_it(self, db_session):
        """A location id and an address are not enough to say what kind of place
        something is. The shop name is the thing a dispatcher recognises."""
        from app.api.routes import unlabelled_docks

        await _dock(db_session, name="Campoli and Sons", samples=12)
        await db_session.flush()

        view = await unlabelled_docks(limit=50, session=db_session, _ops=OPS)

        assert view[0].shop_names == ["Campoli and Sons"]
        assert view[0].inherited_dwell_sample_count == 12
        assert view[0].inherited_dwell_p50_seconds == 120


class TestCoverageIsMeasuredNotAsserted:
    async def test_it_reports_against_the_target(self, db_session):
        from app.api.routes import classification_coverage_view

        await _dock(db_session, name="One")
        await db_session.commit()

        view = await classification_coverage_view(session=db_session, _ops=OPS)

        assert view.shops >= 1
        assert view.unlabelled >= 1
        assert view.meets_target is False

    async def test_a_shop_with_no_dock_is_counted_separately(self, db_session):
        """It cannot be classified at all. Dropping it from the denominator
        would flatter the figure by exactly the accounts we failed to resolve."""
        from app.api.routes import classification_coverage_view

        client = await db_session.scalar(select(Client).limit(1))
        if client is None:
            await _dock(db_session, name="Seed")
            client = await db_session.scalar(select(Client).limit(1))
        db_session.add(
            Shop(
                client_id=client.id, name="No dock", address="N/A", lat=0.0, lng=0.0,
                external_ref=f"REF-{uuid.uuid4().hex[:6]}", location_id=None,
            )
        )
        await db_session.commit()

        view = await classification_coverage_view(session=db_session, _ops=OPS)

        assert view.without_dock >= 1
