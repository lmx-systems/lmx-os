"""A hub can be given the state it is in — which nothing could do.

The audit's last dispatch finding. `Hub.state_code` selects a driver's overtime
rule and was set by nothing: not `app/`, not `scripts/`, not tests. The column's
own comment said why — *"no Hub creation/edit API or UI exists yet (hubs are
seed/DB-provisioned only)"* — so this was a documented gap rather than a hidden
one, and nothing tracked it until the audit did.

Harmless *today*, because `STATE_OVERTIME_RULES` is deliberately empty. The trap
springs the day somebody writes a California daily-overtime rule against real
legal guidance, registers it, tests it in isolation and ships it — and it never
fires, because no hub carries `"CA"`.

`TestTheRuleActuallyFollowsTheState` is the class that matters: it is the only
thing here that would catch the rule not being applied.
"""
import uuid

import pytest

from app.models.hub import US_STATE_CODES, Hub
from app.models.ops_user import ADMIN_ROLE, VIEWER_ROLE
from app.ops_auth.dependencies import AuthedOpsUser
from app.payroll.overtime_rules import (
    STATE_OVERTIME_RULES,
    FederalWeeklyOvertimeRule,
    OvertimeRule,
)
from app.schemas.admin import HubCreateBody, HubUpdateBody

pytestmark = pytest.mark.integration

ADMIN = AuthedOpsUser(
    ops_user_id=str(uuid.uuid4()), email="a@example.com", name="Admin", role=ADMIN_ROLE
)
OPS = AuthedOpsUser(
    ops_user_id="u1", email="v@example.com", name="Viewer", role=VIEWER_ROLE
)


def _body(**overrides) -> HubCreateBody:
    payload = dict(name="Austin Hub", lat=30.27, lng=-97.74)
    payload.update(overrides)
    return HubCreateBody(**payload)


class TestAHubCanBeCreated:
    async def test_it_creates_one_with_its_state(self, db_session):
        """The gap. Before this a hub could only be made by a seed script or by
        hand, and neither set a state."""
        from app.api.admin_routes import create_hub

        view = await create_hub(
            body=_body(state_code="TX"), session=db_session, _admin=ADMIN
        )

        assert view.state_code == "TX"
        hub = await db_session.get(Hub, uuid.UUID(view.id))
        assert hub.state_code == "TX"

    async def test_a_lowercase_code_is_stored_uppercase(self, db_session):
        """`overtime_rule_for_state` upper-cases before looking up, so "ca"
        would work by luck. Storing it normalised means two hubs in the same
        state compare equal."""
        from app.api.admin_routes import create_hub

        view = await create_hub(
            body=_body(state_code="ca"), session=db_session, _admin=ADMIN
        )

        assert view.state_code == "CA"

    async def test_no_state_is_allowed(self, db_session):
        """The one concession. A hub in a state with no registered rule is
        genuinely unaffected, and demanding the field would imply we know what
        to do with it."""
        from app.api.admin_routes import create_hub

        view = await create_hub(body=_body(), session=db_session, _admin=ADMIN)

        assert view.state_code is None

    @pytest.mark.parametrize("bad", ["XX", "ZZ", "Q1"])
    async def test_a_code_that_is_not_a_state_is_refused(self, db_session, bad):
        """Checked against a real list, not a length check. "XX" passes a length
        check and so does a transposed "AR" for "AZ" - and the consequence of a
        wrong one is an overtime rule that does not apply, or one that does."""
        from fastapi import HTTPException

        from app.api.admin_routes import create_hub

        with pytest.raises(HTTPException) as exc:
            await create_hub(
                body=_body(state_code=bad), session=db_session, _admin=ADMIN
            )
        assert exc.value.status_code == 422

    async def test_every_state_is_accepted(self, db_session):
        from app.api.admin_routes import create_hub

        for code in sorted(US_STATE_CODES):
            view = await create_hub(
                body=_body(name=f"Hub {code}", state_code=code),
                session=db_session,
                _admin=ADMIN,
            )
            assert view.state_code == code


class TestAnExistingHubCanBeGivenOne:
    async def test_patching_sets_the_state(self, db_session):
        """Every hub that exists today was provisioned before the column did, so
        this is the only way any of them will ever get one."""
        from app.api.admin_routes import update_hub

        hub = Hub(id=uuid.uuid4(), name="Old Hub", lat=30.0, lng=-97.0)
        db_session.add(hub)
        await db_session.commit()

        view = await update_hub(
            hub_id=hub.id,
            body=HubUpdateBody(state_code="CA"),
            session=db_session,
            _admin=ADMIN,
        )

        assert view.state_code == "CA"

    async def test_an_absent_field_is_left_alone(self, db_session):
        """Absent and null are different. Without that distinction, renaming a
        hub would silently clear its state."""
        from app.api.admin_routes import update_hub

        hub = Hub(id=uuid.uuid4(), name="Old", lat=30.0, lng=-97.0, state_code="CA")
        db_session.add(hub)
        await db_session.commit()

        view = await update_hub(
            hub_id=hub.id,
            body=HubUpdateBody(name="Renamed"),
            session=db_session,
            _admin=ADMIN,
        )

        assert view.name == "Renamed"
        assert view.state_code == "CA", "renaming cleared the state"

    async def test_an_explicit_null_clears_it(self, db_session):
        """So a hub somebody coded wrongly can be corrected back to unset rather
        than being stuck with a wrong answer."""
        from app.api.admin_routes import update_hub

        hub = Hub(id=uuid.uuid4(), name="Old", lat=30.0, lng=-97.0, state_code="CA")
        db_session.add(hub)
        await db_session.commit()

        view = await update_hub(
            hub_id=hub.id,
            body=HubUpdateBody(state_code=None),
            session=db_session,
            _admin=ADMIN,
        )

        assert view.state_code is None

    async def test_an_unknown_hub_is_a_404(self, db_session):
        from fastapi import HTTPException

        from app.api.admin_routes import update_hub

        with pytest.raises(HTTPException) as exc:
            await update_hub(
                hub_id=uuid.uuid4(),
                body=HubUpdateBody(state_code="CA"),
                session=db_session,
                _admin=ADMIN,
            )
        assert exc.value.status_code == 404


class TestTheRuleActuallyFollowsTheState:
    """The class that would catch the failure this finding is about."""

    async def test_a_hub_with_no_state_gets_the_federal_rule(self, db_session):
        from app.api.admin_routes import create_hub

        view = await create_hub(body=_body(), session=db_session, _admin=ADMIN)

        assert view.overtime_rule == "FederalWeeklyOvertimeRule"

    async def test_a_state_with_no_registered_rule_also_gets_federal(self, db_session):
        """"No state set" and "a state with no rule yet" produce identical
        payroll, and only one of them is somebody's oversight - which is why the
        view reports the rule as well as the code."""
        from app.api.admin_routes import create_hub

        view = await create_hub(
            body=_body(state_code="CA"), session=db_session, _admin=ADMIN
        )

        assert view.state_code == "CA"
        assert view.overtime_rule == "FederalWeeklyOvertimeRule"

    async def test_a_registered_rule_reaches_a_hub_that_names_its_state(
        self, db_session, monkeypatch
    ):
        """**The whole point.** Registering a state rule was already possible
        and already tested; nothing could carry it to a hub, because no hub had
        a state. This is the end-to-end the registry never had."""
        from app.api.admin_routes import create_hub

        class CaliforniaDailyOvertime(OvertimeRule):
            def apply(self, daily_hours):  # pragma: no cover - not exercised here
                return 0.0, 0.0

        monkeypatch.setitem(STATE_OVERTIME_RULES, "CA", CaliforniaDailyOvertime())

        view = await create_hub(
            body=_body(state_code="CA"), session=db_session, _admin=ADMIN
        )

        assert view.overtime_rule == "CaliforniaDailyOvertime"

    async def test_the_registry_is_still_empty_in_the_real_tree(self):
        """Nothing here registers a state rule for real. A rule goes in only
        once it has been written against actual legal guidance and the business
        decision to switch it on has been made - see
        `docs/PAYROLL_STATE_OT_RESEARCH.md`."""
        assert STATE_OVERTIME_RULES == {}
        assert isinstance(
            __import__("app.payroll.overtime_rules", fromlist=["x"]).overtime_rule_for_state(None),
            FederalWeeklyOvertimeRule,
        )


class TestWhoMayChangeIt:
    async def test_creating_needs_an_admin(self):
        import inspect

        from app.api.admin_routes import create_hub
        from app.ops_auth.dependencies import require_admin

        dependency = inspect.signature(create_hub).parameters["_admin"].default
        assert dependency.dependency is require_admin

    async def test_updating_needs_an_admin(self):
        import inspect

        from app.api.admin_routes import update_hub
        from app.ops_auth.dependencies import require_admin

        dependency = inspect.signature(update_hub).parameters["_admin"].default
        assert dependency.dependency is require_admin

    async def test_reading_is_open_to_any_ops_session(self, db_session):
        """Somebody wondering why a driver's overtime looks wrong should not
        need an admin to find out which rule applies."""
        import inspect

        from app.api.admin_routes import get_hub
        from app.ops_auth.dependencies import get_current_ops_user

        dependency = inspect.signature(get_hub).parameters["_ops"].default
        assert dependency.dependency is get_current_ops_user

        hub = Hub(id=uuid.uuid4(), name="Readable", lat=30.0, lng=-97.0)
        db_session.add(hub)
        await db_session.commit()

        view = await get_hub(hub_id=hub.id, session=db_session, _ops=OPS)
        assert view.name == "Readable"
