"""
Rate cards as versioned, effective-dated data (T2.5 A1), against real Postgres/Redis.

The defect this closes is narrow and easy to miss: orders were never at risk, because
`fee_cents` and `fee_breakdown` are frozen at ingestion. What a rate edit destroyed was
the *card's own* history - after it, nothing could say what the rate had been last week,
or which version priced a given drop. Both are the audit trail `H1` asks for.

So most of what is asserted here is that the old version still exists and still says what
it said.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.admin_routes import list_client_rates, upsert_client_rate
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.hub import Hub
from app.schemas.admin import ClientRateBody

pytestmark = pytest.mark.integration


async def _seed_client(db_session) -> Client:
    hub = Hub(id=uuid.uuid4(), name="Rate Hub", timezone="UTC", lat=30.26, lng=-97.74)
    db_session.add(hub)
    # Flushed before the client is built: hub_id is set from the value rather than through
    # a relationship, so SQLAlchemy has no dependency to order the inserts by.
    await db_session.flush()
    client = Client(id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file")
    db_session.add(client)
    await db_session.commit()
    return client


def _body(cents: int) -> ClientRateBody:
    return ClientRateBody(
        sla_tier="T2",
        rate_per_drop_cents=cents,
        rate_per_mile_cents=0,
        rate_per_piece_cents=0,
        rate_per_weight_unit_cents=0,
        minimum_charge_cents=None,
    )


async def _versions(db_session, client_id) -> list[ClientRate]:
    rows = await db_session.execute(
        select(ClientRate)
        .where(ClientRate.client_id == client_id, ClientRate.sla_tier == "T2")
        .order_by(ClientRate.effective_from)
    )
    return list(rows.scalars().all())


async def test_changing_a_rate_keeps_the_old_version(db_session, real_redis_client):
    """The whole point. Before 0045 this UPDATEd one row and the 1800 was gone."""
    client = await _seed_client(db_session)

    await upsert_client_rate(str(client.id), _body(1800), session=db_session, _admin=None)
    await upsert_client_rate(str(client.id), _body(1950), session=db_session, _admin=None)

    versions = await _versions(db_session, client.id)
    assert [v.rate_per_drop_cents for v in versions] == [1800, 1950]
    assert versions[0].effective_from < versions[1].effective_from


async def test_the_listing_shows_one_rate_per_tier_not_every_version(db_session, real_redis_client):
    """A history is not a list of duplicate rates.

    Three versions of T2 must read as one current T2 rate, or the dashboard shows the same
    tier three times and nobody can tell which one applies.
    """
    client = await _seed_client(db_session)
    for cents in (1800, 1900, 2000):
        await upsert_client_rate(str(client.id), _body(cents), session=db_session, _admin=None)

    listed = await list_client_rates(str(client.id), session=db_session, _admin=None)

    assert len(listed) == 1
    assert listed[0].sla_tier == "T2"
    assert listed[0].rate_per_drop_cents == 2000

    # But the history is all still there.
    assert len(await _versions(db_session, client.id)) == 3


async def test_a_future_version_does_not_become_todays_rate(db_session, real_redis_client):
    """A negotiated increase can be entered when it is agreed, not on the morning it starts.

    This is what effective-dating buys beyond an audit trail, and it is why the lookup
    filters on `effective_from <= now` rather than just taking the newest row.
    """
    client = await _seed_client(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(
        ClientRate(
            client_id=client.id,
            sla_tier="T2",
            rate_per_drop_cents=1800,
            effective_from=now - timedelta(days=30),
        )
    )
    db_session.add(
        ClientRate(
            client_id=client.id,
            sla_tier="T2",
            rate_per_drop_cents=2500,
            effective_from=now + timedelta(days=30),
        )
    )
    await db_session.commit()

    listed = await list_client_rates(str(client.id), session=db_session, _admin=None)
    assert len(listed) == 1
    assert listed[0].rate_per_drop_cents == 1800, "the future rate must not price today"


async def test_two_versions_of_one_tier_cannot_start_at_the_same_instant(
    db_session, real_redis_client
):
    """Two rates in force at once is a contradiction, not history.

    The unique constraint moved from (client, tier) to (client, tier, effective_from);
    this is the half of it that still refuses.
    """
    client = await _seed_client(db_session)
    at = datetime.now(timezone.utc)

    db_session.add(
        ClientRate(
            client_id=client.id, sla_tier="T2", rate_per_drop_cents=1800, effective_from=at
        )
    )
    await db_session.commit()

    db_session.add(
        ClientRate(
            client_id=client.id, sla_tier="T2", rate_per_drop_cents=1950, effective_from=at
        )
    )
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


async def test_different_tiers_are_independent(db_session, real_redis_client):
    """Editing T2 must not disturb T1's history - they version separately."""
    client = await _seed_client(db_session)

    t1 = ClientRateBody(
        sla_tier="T1",
        rate_per_drop_cents=2400,
        rate_per_mile_cents=0,
        rate_per_piece_cents=0,
        rate_per_weight_unit_cents=0,
        minimum_charge_cents=None,
    )
    await upsert_client_rate(str(client.id), t1, session=db_session, _admin=None)
    await upsert_client_rate(str(client.id), _body(1800), session=db_session, _admin=None)
    await upsert_client_rate(str(client.id), _body(1950), session=db_session, _admin=None)

    listed = {r.sla_tier: r.rate_per_drop_cents for r in
              await list_client_rates(str(client.id), session=db_session, _admin=None)}
    assert listed == {"T1": 2400, "T2": 1950}
