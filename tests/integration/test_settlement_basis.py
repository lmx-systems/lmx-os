"""STL-2: what a statement was calculated under, frozen and versioned.

The test that carries the item is `TestTheProblemRecomputeCreated`. `--recompute`
supersedes costs after a statement has been issued, so the same command over the
same period can produce a different figure - and a customer holding the first
one has a dispute. These assert that the difference is detectable and
attributable, rather than silent.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.settlement_basis import SIDE_CUSTOMER, SIDE_LMX, SettlementBasis
from app.settle.basis import (
    BASIS_VERSION,
    NotSigned,
    cost_fingerprint,
    freeze_statement_basis,
    issue,
    live_basis,
    reproduce,
    require_agreed,
    sign,
)
from app.settle.statement import ArmComparison, ArmSample, SavingsStatement
from sqlalchemy import select

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _statement(client_id=None, *, costs=None, difference=337.0) -> SavingsStatement:
    return SavingsStatement(
        client_id=client_id or uuid.uuid4(),
        period_start=START,
        period_end=END,
        drops=400,
        costed_drops=len(costs or {}) or 400,
        cost_per_drop_cents=1000.0,
        comparison=ArmComparison(
            control=ArmSample("control", 40, 1100.0, 400.0),
            treatment=ArmSample("treatment", 360, 1100.0 - difference, 400.0),
            difference_cents=difference,
            half_width_cents=130.0,
        ),
        comparison_unavailable=None,
        costed_orders=costs if costs is not None else {"a": 1000, "b": 1200},
    )


class TestTheProblemRecomputeCreated:
    async def test_an_unchanged_statement_reproduces(self, db_session):
        statement = _statement()
        basis = await issue(db_session, statement, now=NOW)
        assert reproduce(basis, statement).reproduces is True

    async def test_a_recosted_order_is_detected_and_named(self, db_session):
        """The dispute scenario. The method did not change; a cost underneath it
        did, and the customer is holding the old figure."""
        client = uuid.uuid4()
        issued = _statement(client, costs={"a": 1000, "b": 1200})
        basis = await issue(db_session, issued, now=NOW)

        recosted = _statement(client, costs={"a": 1000, "b": 1450})
        result = reproduce(basis, recosted)

        assert result.reproduces is False
        assert result.costs_match is False
        assert result.recosted_orders == ["b"]
        assert "1 order(s) were recosted" in result.explain()

    async def test_an_order_appearing_late_is_detected(self, db_session):
        """A geofence event arriving after issue adds a cost that was not there.
        A digest over the set catches it; a count would not."""
        client = uuid.uuid4()
        basis = await issue(db_session, _statement(client, costs={"a": 1000}), now=NOW)
        result = reproduce(basis, _statement(client, costs={"a": 1000, "c": 900}))
        assert result.recosted_orders == ["c"]

    async def test_a_changed_method_is_distinguished_from_a_changed_cost(
        self, db_session
    ):
        """The distinction the table exists to preserve. Folding costs into
        `inputs_hash` would have made every recomputation look like a change of
        definition."""
        client = uuid.uuid4()
        basis = await issue(db_session, _statement(client, difference=337.0), now=NOW)
        moved = reproduce(basis, _statement(client, difference=500.0))
        assert moved.definition_matches is False
        assert moved.costs_match is True
        assert "difference_cents" in moved.changed_figures
        assert moved.changed_figures["difference_cents"]["was"] == 337.0


class TestVersioning:
    async def test_the_first_issue_is_version_one(self, db_session):
        basis = await issue(db_session, _statement(), now=NOW)
        assert basis.version == 1
        assert basis.inputs["version"] == BASIS_VERSION

    async def test_reissuing_an_unchanged_statement_does_not_mint_a_version(
        self, db_session
    ):
        """A version number that incremented every time somebody re-ran a script
        would stop meaning 'the definition changed', which is the only thing it
        is for."""
        client = uuid.uuid4()
        statement = _statement(client)
        first = await issue(db_session, statement, now=NOW)
        second = await issue(db_session, statement, now=NOW + timedelta(days=1))
        assert first.id == second.id
        assert second.version == 1

    async def test_a_change_supersedes_rather_than_edits(self, db_session):
        """'We changed how this was calculated in October' has to survive the
        change."""
        client = uuid.uuid4()
        first = await issue(db_session, _statement(client, difference=337.0), now=NOW)
        second = await issue(
            db_session, _statement(client, difference=500.0),
            now=NOW + timedelta(days=1),
        )
        assert second.version == 2
        assert first.superseded_at is not None
        assert first.is_live is False
        assert second.is_live is True

    async def test_only_one_basis_is_live_per_period(self, db_session):
        client = uuid.uuid4()
        await issue(db_session, _statement(client, difference=337.0), now=NOW)
        await issue(db_session, _statement(client, difference=500.0), now=NOW)
        live = list(
            await db_session.scalars(
                select(SettlementBasis).where(
                    SettlementBasis.client_id == client,
                    SettlementBasis.superseded_at.is_(None),
                )
            )
        )
        assert len(live) == 1

    async def test_the_live_basis_can_be_looked_up(self, db_session):
        client = uuid.uuid4()
        basis = await issue(db_session, _statement(client), now=NOW)
        found = await live_basis(
            db_session, client_id=client, period_start=START, period_end=END
        )
        assert found is not None and found.id == basis.id


class TestSignatures:
    async def test_one_signature_is_a_draft(self, db_session):
        basis = await issue(db_session, _statement(), now=NOW)
        await sign(db_session, basis, side=SIDE_LMX, who="Sourabh", now=NOW)
        assert basis.is_agreed is False
        with pytest.raises(NotSigned, match="customer has not signed"):
            require_agreed(basis)

    async def test_both_sides_make_it_agreed(self, db_session):
        basis = await issue(db_session, _statement(), now=NOW)
        await sign(db_session, basis, side=SIDE_LMX, who="Sourabh", now=NOW)
        await sign(db_session, basis, side=SIDE_CUSTOMER, who="Ops Manager", now=NOW)
        assert basis.is_agreed is True
        assert require_agreed(basis) is basis

    async def test_a_signature_needs_a_name(self, db_session):
        basis = await issue(db_session, _statement(), now=NOW)
        with pytest.raises(ValueError, match="needs a name"):
            await sign(db_session, basis, side=SIDE_LMX, who="   ", now=NOW)

    async def test_an_unknown_side_is_refused(self, db_session):
        basis = await issue(db_session, _statement(), now=NOW)
        with pytest.raises(ValueError, match="side must be"):
            await sign(db_session, basis, side="auditor", who="X", now=NOW)

    async def test_no_basis_at_all_is_its_own_refusal(self):
        with pytest.raises(NotSigned, match="no basis has been issued"):
            require_agreed(None)

    async def test_the_database_refuses_half_a_signature(self, db_session):
        """A name without a date looks signed to a query and is not."""
        from sqlalchemy import text

        await db_session.commit()
        with pytest.raises(Exception, match="ck_settlement_bases_lmx_signature"):
            await db_session.execute(
                text(
                    "INSERT INTO settlement_bases (id, client_id, version, "
                    "period_start, period_end, issued_at, inputs, inputs_hash, "
                    "cost_fingerprint, costs, lmx_signed_by) VALUES "
                    "(:id, :c, 1, :s, :e, :n, '{}', 'h', 'f', '{}', 'Nobody')"
                ),
                {"id": uuid.uuid4(), "c": uuid.uuid4(), "s": START, "e": END, "n": NOW},
            )
        await db_session.rollback()

    async def test_a_superseded_basis_keeps_its_signatures(self, db_session):
        """The record of what was agreed in August has to survive October."""
        client = uuid.uuid4()
        first = await issue(db_session, _statement(client, difference=337.0), now=NOW)
        await sign(db_session, first, side=SIDE_LMX, who="Sourabh", now=NOW)
        await sign(db_session, first, side=SIDE_CUSTOMER, who="Ops", now=NOW)
        await issue(db_session, _statement(client, difference=500.0), now=NOW)
        assert first.is_agreed is True
        assert first.superseded_at is not None


class TestTheFrozenDefinition:
    def test_it_holds_the_method_not_the_weather(self):
        frozen = freeze_statement_basis(_statement())
        assert frozen["cost_method"]["unit"] == "driver-day, loaded"
        assert "fuel" in frozen["cost_method"]["excludes"]
        assert frozen["comparison_floor_drops_per_arm"] == 30

    def test_the_fingerprint_ignores_row_order(self):
        """Two runs that saw the same costs must hash alike whatever order the
        rows came back in, or the digest is a nonce."""
        assert cost_fingerprint({"a": 1, "b": 2}) == cost_fingerprint({"b": 2, "a": 1})

    def test_the_fingerprint_moves_when_a_cost_does(self):
        assert cost_fingerprint({"a": 1}) != cost_fingerprint({"a": 2})

    async def test_a_statement_with_no_comparison_can_still_be_issued(self, db_session):
        """Most periods will have no figure - the arm is off. A basis is still
        worth recording: it says what we would have measured and under what."""
        statement = _statement()
        statement.comparison = None
        basis = await issue(db_session, statement, now=NOW)
        assert basis.inputs["figures"]["shows_a_saving"] is False
