"""
GET /hubs (roadmap item D1) against real Postgres - calls the route
function directly, same pattern as the other integration tests.
"""
import uuid

import pytest

from app.api.routes import list_hubs
from app.models.hub import Hub

pytestmark = pytest.mark.integration


async def test_list_hubs_returns_active_hubs_sorted_by_name(db_session):
    suffix = uuid.uuid4().hex[:8]
    db_session.add(Hub(id=uuid.uuid4(), name=f"Zed Hub {suffix}", lat=34.0, lng=-118.0))
    db_session.add(Hub(id=uuid.uuid4(), name=f"Alpha Hub {suffix}", lat=40.7, lng=-74.0))
    await db_session.commit()

    hubs = await list_hubs(session=db_session)
    names = [h.name for h in hubs if suffix in h.name]
    assert names == [f"Alpha Hub {suffix}", f"Zed Hub {suffix}"]
    assert all(h.active for h in hubs)


async def test_list_hubs_excludes_inactive_by_default_but_can_include(db_session):
    suffix = uuid.uuid4().hex[:8]
    inactive_id = uuid.uuid4()
    db_session.add(Hub(id=inactive_id, name=f"Closed Hub {suffix}", lat=1.0, lng=1.0, active=False))
    await db_session.commit()

    default_hubs = await list_hubs(session=db_session)
    assert str(inactive_id) not in [h.hub_id for h in default_hubs]

    all_hubs = await list_hubs(include_inactive=True, session=db_session)
    assert str(inactive_id) in [h.hub_id for h in all_hubs]
