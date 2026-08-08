"""
Scheduler-callable triggers (app/api/internal_routes.py).

These exist because dispatch is event-driven off an in-process poll loop, and a
serverless platform suspends that loop between requests - so orders can sit in the
hold queue with nothing to release them. The primary fix is deployment config;
these are the safety net.

The security shape is what most of these tests are about. An unauthenticated
run-cycle endpoint would be both a denial-of-service lever and a way to move real
work, so the router must fail CLOSED when no secret is configured rather than
open - and "nobody set the secret" is exactly the deployment where that matters.
"""
import uuid

import pytest
from fastapi import HTTPException

from app.api.internal_routes import (
    require_internal_secret,
    run_dispatch_for_all_hubs,
    run_learning_loop_for_all_hubs,
)
from app.config import settings
from app.models.hub import Hub

pytestmark = pytest.mark.integration

TOKEN = "a-real-internal-token-value"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", TOKEN)


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", None)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


async def test_the_correct_secret_is_accepted(configured):
    await require_internal_secret(x_lmx_internal_token=TOKEN)


async def test_a_wrong_secret_is_refused(configured):
    with pytest.raises(HTTPException) as exc:
        await require_internal_secret(x_lmx_internal_token="not-the-token")
    assert exc.value.status_code == 404


async def test_a_missing_header_is_refused(configured):
    with pytest.raises(HTTPException) as exc:
        await require_internal_secret(x_lmx_internal_token=None)
    assert exc.value.status_code == 404


async def test_with_no_secret_configured_the_routes_fail_closed(unconfigured):
    """The property that matters most. A deployment that forgot to set a secret
    must not expose an open run-cycle endpoint - and supplying any token, or
    none, must both be refused."""
    for attempt in (None, "", "anything-at-all"):
        with pytest.raises(HTTPException) as exc:
            await require_internal_secret(x_lmx_internal_token=attempt)
        assert exc.value.status_code == 404


async def test_the_refusal_is_a_404_not_a_401(configured):
    """So an unauthenticated prober can't confirm these routes exist. Nothing
    legitimate finds them by probing."""
    with pytest.raises(HTTPException) as exc:
        await require_internal_secret(x_lmx_internal_token="wrong")
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


# ---------------------------------------------------------------------------
# Running across hubs
# ---------------------------------------------------------------------------


async def test_dispatch_runs_for_every_active_hub(db_session, real_redis_client, configured):
    """Per-hub isn't the shape: the scheduler shouldn't hardcode hub ids, and a
    newly onboarded hub should be covered the moment it exists."""
    ids = []
    for name in ("Austin Hub", "Dallas Hub"):
        hub_id = uuid.uuid4()
        db_session.add(Hub(id=hub_id, name=name, lat=30.267, lng=-97.743))
        ids.append(str(hub_id))
    await db_session.commit()

    result = await run_dispatch_for_all_hubs(session=db_session)

    assert set(result["hubs"]) == set(ids)
    # Nothing to assign, which is the common case and has to be cheap rather than
    # an error - that is what makes this safe to over-call.
    assert all(v == 0 for v in result["hubs"].values())


async def test_an_inactive_hub_is_skipped(db_session, real_redis_client, configured):
    active, inactive = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=active, name="Active Hub", lat=30.2, lng=-97.7))
    db_session.add(Hub(id=inactive, name="Closed Hub", lat=32.7, lng=-96.8, active=False))
    await db_session.commit()

    result = await run_dispatch_for_all_hubs(session=db_session)

    assert str(active) in result["hubs"]
    assert str(inactive) not in result["hubs"]


async def test_no_hubs_is_not_an_error(db_session, real_redis_client, configured):
    """A fresh deployment has none, and the scheduler will still be calling."""
    assert await run_dispatch_for_all_hubs(session=db_session) == {"hubs": {}}


async def test_one_failing_hub_does_not_stop_the_others(
    db_session, real_redis_client, configured, monkeypatch
):
    """A bad hub's data must not strand every other hub's orders."""
    good, bad = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=good, name="Good Hub", lat=30.2, lng=-97.7))
    db_session.add(Hub(id=bad, name="Bad Hub", lat=32.7, lng=-96.8))
    await db_session.commit()

    from app.optimizer.service import DispatchOptimizerService

    original = DispatchOptimizerService.run_cycle

    async def selective(self, hub_id: str):
        if hub_id == str(bad):
            raise RuntimeError("this hub's state is broken")
        return await original(self, hub_id)

    monkeypatch.setattr(DispatchOptimizerService, "run_cycle", selective)

    result = await run_dispatch_for_all_hubs(session=db_session)

    assert result["hubs"][str(good)] == 0
    assert "error" in str(result["hubs"][str(bad)])


async def test_the_learning_loop_runs_across_hubs_too(db_session, real_redis_client, configured):
    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()

    result = await run_learning_loop_for_all_hubs(session=db_session)

    assert str(hub_id) in result["hubs"]
    # No driver annotations yet, so nothing to propose - and calling it twice
    # must not double-propose, which is what makes it safe as a safety net.
    assert result["hubs"][str(hub_id)] == 0
    again = await run_learning_loop_for_all_hubs(session=db_session)
    assert again["hubs"][str(hub_id)] == 0
