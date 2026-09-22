"""Provisioning a driver, which nothing could do.

`docs/ROADMAP_AUDIT_2026-09.md` found that no endpoint and no script creates a
`Driver` — every row was a hand-written database insert. Meanwhile
`app/api/driver_routes.py`'s OTP path says in its own comment that *"drivers are
provisioned by ops, not self-registered"*. The provisioning it refers to did not
exist, so the sentence described an intention rather than a route.

Two things here are load-bearing rather than form-filling: the phone is the login
identity and must be unique, and the capacity is what the router reads.
"""
import uuid

import pytest
from sqlalchemy import select, text

from app.models.driver import EMPLOYMENT_TYPES, VEHICLE_TYPES, Driver
from app.models.hub import Hub
from app.models.ops_user import ADMIN_ROLE, VIEWER_ROLE
from app.ops_auth.dependencies import AuthedOpsUser
from app.schemas.admin import DriverOnboardingBody

pytestmark = pytest.mark.integration

ADMIN = AuthedOpsUser(
    ops_user_id=str(uuid.uuid4()), email="a@example.com", name="Admin", role=ADMIN_ROLE
)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Onboarding Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.commit()
    return hub


def _body(hub, **overrides) -> DriverOnboardingBody:
    payload = dict(
        hub_id=str(hub.id),
        name="Sam Okafor",
        phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
        vehicle_capacity_units=8,
        employment_type="w2",
    )
    payload.update(overrides)
    return DriverOnboardingBody(**payload)


class TestADriverCanBeCreated:
    async def test_it_creates_one(self, db_session):
        """The gap, in one assertion. Before this a driver could only be made by
        writing SQL by hand."""
        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)

        result = await onboard_driver(
            body=_body(hub), session=db_session, _admin=ADMIN
        )

        driver = await db_session.get(Driver, uuid.UUID(result.driver_id))
        assert driver is not None
        assert driver.name == "Sam Okafor"
        assert driver.hub_id == hub.id

    async def test_the_capacity_is_what_was_asked_for(self, db_session):
        """The column defaults to 1 and the optimizer's capacity check reads it,
        so a driver provisioned without thinking about it gets one order at a
        time. The field is required for that reason."""
        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)

        result = await onboard_driver(
            body=_body(hub, vehicle_capacity_units=12), session=db_session, _admin=ADMIN
        )

        assert result.vehicle_capacity_units == 12
        driver = await db_session.get(Driver, uuid.UUID(result.driver_id))
        assert driver.vehicle_capacity_units == 12

    async def test_capacity_is_not_optional(self, db_session):
        """Pydantic refuses it before the handler runs. A default here would be
        the silent 1 all over again, one layer up."""
        from pydantic import ValidationError

        hub = await _hub(db_session)

        with pytest.raises(ValidationError):
            DriverOnboardingBody(
                hub_id=str(hub.id), name="Sam", phone="+15555550100",
                employment_type="w2",
            )

    async def test_a_missing_hourly_rate_is_said_out_loud(self, db_session):
        """A driver paid from a placeholder is a number somebody will later have
        to defend. Better to know at provisioning than at month end."""
        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)

        result = await onboard_driver(
            body=_body(hub), session=db_session, _admin=ADMIN
        )

        assert result.hourly_rate_is_placeholder is True

    async def test_a_real_rate_is_not_a_placeholder(self, db_session):
        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)

        result = await onboard_driver(
            body=_body(hub, hourly_rate_cents=2400), session=db_session, _admin=ADMIN
        )

        assert result.hourly_rate_is_placeholder is False


class TestThePhoneIsTheLoginIdentity:
    async def test_a_duplicate_number_is_refused_readably(self, db_session):
        """OTP looks a driver up by number with `scalar_one_or_none`, which
        raises on two rows — so a duplicate locks **both** drivers out of the
        app with a 500, not one of them with an error."""
        from fastapi import HTTPException

        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)
        first = _body(hub, phone="+15555550199")
        await onboard_driver(body=first, session=db_session, _admin=ADMIN)

        with pytest.raises(HTTPException) as exc:
            await onboard_driver(
                body=_body(hub, phone="+15555550199", name="Someone Else"),
                session=db_session,
                _admin=ADMIN,
            )

        assert exc.value.status_code == 409
        assert "lock both" in exc.value.detail

    async def test_the_database_refuses_it_too(self, db_session):
        """The endpoint is not the only writer and, on today's evidence, not
        even the usual one — every existing driver is a hand-written insert.
        Migration `0063` is what actually guarantees this."""
        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)
        await onboard_driver(
            body=_body(hub, phone="+15555550177"), session=db_session, _admin=ADMIN
        )
        await db_session.commit()

        with pytest.raises(Exception, match="uq_drivers_phone"):
            await db_session.execute(
                text(
                    "INSERT INTO drivers (id, hub_id, name, phone, "
                    "vehicle_capacity_units, status, employment_type) VALUES "
                    "(:id, :hub, 'Hand inserted', '+15555550177', 5, 'off_shift', 'w2')"
                ),
                {"id": uuid.uuid4(), "hub": hub.id},
            )
        await db_session.rollback()

    async def test_the_number_is_trimmed_before_it_is_stored(self, db_session):
        """A trailing space makes a number that looks identical and does not
        match at login."""
        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)

        result = await onboard_driver(
            body=_body(hub, phone="  +15555550166  "), session=db_session, _admin=ADMIN
        )

        assert result.phone == "+15555550166"


class TestTheVocabularyIsClosed:
    @pytest.mark.parametrize("employment_type", EMPLOYMENT_TYPES)
    async def test_every_employment_type_is_accepted(self, db_session, employment_type):
        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)

        result = await onboard_driver(
            body=_body(hub, employment_type=employment_type),
            session=db_session,
            _admin=ADMIN,
        )

        assert result.employment_type == employment_type

    async def test_an_invented_employment_type_is_refused(self, db_session):
        """`admin_routes.py` and `driver_routes.py` both branch on `== "gig"`.
        A fourth value arriving by typo would take a driver down a path nobody
        wrote."""
        from fastapi import HTTPException

        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)

        with pytest.raises(HTTPException) as exc:
            await onboard_driver(
                body=_body(hub, employment_type="intern"),
                session=db_session,
                _admin=ADMIN,
            )
        assert exc.value.status_code == 422

    async def test_an_invented_vehicle_type_is_refused(self, db_session):
        from fastapi import HTTPException

        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)

        with pytest.raises(HTTPException) as exc:
            await onboard_driver(
                body=_body(hub, vehicle_type="hovercraft"),
                session=db_session,
                _admin=ADMIN,
            )
        assert exc.value.status_code == 422

    async def test_no_vehicle_is_allowed_because_the_app_asks_later(self, db_session):
        """Null means "setup incomplete" and the driver app routes there. It is
        deliberately not defaulted, so provisioning must be able to leave it."""
        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)

        result = await onboard_driver(
            body=_body(hub, vehicle_type=None), session=db_session, _admin=ADMIN
        )

        driver = await db_session.get(Driver, uuid.UUID(result.driver_id))
        assert driver.vehicle_type is None

    async def test_the_console_offers_exactly_what_the_api_accepts(self):
        """A drift between the dropdown and the vocabulary is invisible until
        somebody picks the one value that is refused."""
        import pathlib
        import re

        types_ts = (
            pathlib.Path(__file__).resolve().parents[2]
            / "dashboard/src/lib/types.ts"
        ).read_text()
        block = types_ts.split("EMPLOYMENT_TYPES")[1].split("]")[0]
        offered = re.findall(r"code: '([a-z0-9_]+)'", block)

        assert offered == list(EMPLOYMENT_TYPES)

        vehicles = types_ts.split("VEHICLE_TYPES")[1].split("]")[0]
        assert re.findall(r"'([a-z]+)'", vehicles) == list(VEHICLE_TYPES)


class TestWhoMayProvision:
    async def test_an_unknown_hub_is_a_404(self, db_session):
        from fastapi import HTTPException

        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)
        body = _body(hub, hub_id=str(uuid.uuid4()))

        with pytest.raises(HTTPException) as exc:
            await onboard_driver(body=body, session=db_session, _admin=ADMIN)
        assert exc.value.status_code == 404

    async def test_it_needs_an_admin(self):
        """It creates the identity a person logs in with, and every capacity,
        payroll and document decision hangs off it. Same bar as onboarding a
        client."""
        import inspect

        from app.api.admin_routes import onboard_driver
        from app.ops_auth.dependencies import require_admin

        dependency = inspect.signature(onboard_driver).parameters["_admin"].default
        assert dependency.dependency is require_admin

    async def test_a_viewer_is_not_an_admin(self, db_session):
        """Asserting the role check does something, not just that it is wired."""
        from fastapi import HTTPException

        from app.ops_auth.dependencies import require_admin

        viewer = AuthedOpsUser(
            ops_user_id="u1", email="v@example.com", name="V", role=VIEWER_ROLE
        )

        with pytest.raises(HTTPException) as exc:
            await require_admin(ops_user=viewer)
        assert exc.value.status_code == 403


class TestTheDriverCanThenLogIn:
    async def test_a_provisioned_driver_is_found_by_their_number(self, db_session):
        """The point of provisioning. Before this the OTP path's 404 - "no
        driver registered with this phone number" - was the only possible
        answer, because nothing registered one."""
        from app.api.admin_routes import onboard_driver

        hub = await _hub(db_session)
        result = await onboard_driver(
            body=_body(hub, phone="+15555550144"), session=db_session, _admin=ADMIN
        )
        await db_session.commit()

        found = await db_session.scalar(
            select(Driver).where(Driver.phone == "+15555550144")
        )
        assert found is not None
        assert str(found.id) == result.driver_id
