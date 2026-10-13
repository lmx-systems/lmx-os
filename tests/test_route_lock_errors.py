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
