"""Every ops write is counted, and every one says what kind of work it is (`DEC-7`).

"Person touches per 100 orders" is only the distance to nobody running the day
if it counts every write a person can make. Two ways it could quietly stop: a
router carrying ops writes without the recording dependency, or a new write with
no category, which would be counted as `unclassified` and left out of the
headline. Both fail here, when the write is added.
"""
from fastapi.routing import APIRoute

from app.api import admin_routes, routes
from app.ingestion import router as ingestion
from app.models.ops_touch import CATEGORIES, CATEGORY_UNCLASSIFIED
from app.ops_auth.dependencies import get_current_ops_user, require_admin, require_dispatcher
from app.playbook.touches import TOUCH_CATEGORIES, WRITE_METHODS, record_ops_touch

ROUTERS = (routes.router, admin_routes.router, ingestion.router)
_GUARDS = (require_admin, require_dispatcher, get_current_ops_user)


def _calls(dependant) -> set:
    found = set()
    for sub in dependant.dependencies:
        found.add(sub.call)
        found |= _calls(sub)
    return found


def _ops_writes():
    for router in ROUTERS:
        for route in router.routes:
            if isinstance(route, APIRoute) and route.methods & WRITE_METHODS:
                if any(guard in _calls(route.dependant) for guard in _GUARDS):
                    yield route


def test_each_router_records_touches():
    for router in ROUTERS:
        assert any(dep.dependency is record_ops_touch for dep in router.dependencies), router.prefix


def test_every_ops_write_has_a_category():
    writes = list(_ops_writes())
    assert len(writes) >= 45  # guards the check against finding nothing
    missing = sorted(route.name for route in writes if route.name not in TOUCH_CATEGORIES)
    assert not missing, f"Ops writes with no touch category (app/playbook/touches.py): {missing}"


def test_the_categories_name_real_writes():
    """A stale entry - a write renamed or removed - is a category for nothing."""
    names = {route.name for route in _ops_writes()}
    stale = sorted(set(TOUCH_CATEGORIES) - names)
    assert not stale, stale


def test_every_category_is_one_the_table_accepts():
    assert set(TOUCH_CATEGORIES.values()) <= set(CATEGORIES) - {CATEGORY_UNCLASSIFIED}
