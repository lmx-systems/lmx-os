"""`try_lock_route` gives up only on a busy route (`CON-5`, third review).

It caught every database error as "somebody is on this route", so a dropped
connection or a statement timeout reached the dispatcher as "try again in a
moment" instead of an error. Only Postgres's lock_not_available (55P03), which
NOWAIT raises, means busy.
"""
import asyncio
import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from app.delivery.routes import try_lock_route, try_lock_route_and_stops


class _PgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class _Session:
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        self.rolled_back = False

    async def execute(self, *_args, **_kwargs):
        raise DBAPIError("SELECT ... FOR UPDATE NOWAIT", {}, _PgError(self.sqlstate))

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.parametrize("attempt", [try_lock_route, try_lock_route_and_stops])
def test_a_busy_route_is_a_refusal(attempt):
    session = _Session("55P03")
    assert asyncio.run(attempt(session, uuid.uuid4())) is False
    assert session.rolled_back


@pytest.mark.parametrize("attempt", [try_lock_route, try_lock_route_and_stops])
def test_any_other_database_error_is_raised(attempt):
    session = _Session("57014")  # query_canceled: a statement timeout
    with pytest.raises(DBAPIError):
        asyncio.run(attempt(session, uuid.uuid4()))
    assert not session.rolled_back


class _Redis:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def set(self, key, _value, nx=False, ex=None):
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True

    async def delete(self, key):
        self.keys.discard(key)


class _PingSession(_Session):
    async def scalar(self, *_args, **_kwargs):
        return uuid.uuid4()


def test_a_failed_ping_refresh_leaves_the_next_ping_free_to_try(monkeypatch):
    """The once-a-minute key was left set when the lock attempt raised, so a
    database error cost the route a minute without a refresh."""
    from app.delivery import eta

    redis = _Redis()
    monkeypatch.setattr(eta, "get_client", lambda: redis)
    with pytest.raises(DBAPIError):
        asyncio.run(eta.refresh_after_ping(_PingSession("57014"), uuid.uuid4()))
    assert redis.keys == set()
