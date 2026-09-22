"""No column is read for a decision that nothing ever writes.

The sibling of `tests/test_no_new_orphans.py`, for the shape that check cannot
see. `OrderStatus.queued` was read by `CON-4`'s exception queue and written by
nothing, so one of its four kinds could never fire - and no function was
orphaned, so nothing caught it.

**A column read and never written is worse than a dead one.** Dead is merely
unused; read-and-never-written is a permanent silent default that looks like a
value somebody chose. `Hub.state_code` selects an overtime rule and is set
nowhere, so every hub is federal-only for ever — and the day somebody registers
a California rule, tests it, and ships it, it will never fire.

## What counts as a write

Assignment to an attribute (`x.foo = `, `x.foo += `, tuple unpacking), or a
keyword argument anywhere outside `app/models/`. Keyword arguments are counted
because that is how most rows are constructed, and it is loose - `limit=10`
counts as writing a column called `limit` if one existed. Loose in the safe
direction: this check under-reports rather than inventing work.

Two earlier versions of this were regex and got it wrong in both directions,
reporting `pin_verification_attempts += 1` and `basis.lmx_signed_by, ... = ...`
as never written. It walks the syntax tree for that reason.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"
MODELS = APP / "models"

# Columns nothing writes, with why. Each is a real gap or a stated hold.
KNOWN_UNWRITTEN: dict[str, str] = {
    # The one with teeth. Read by `app/payroll/hours.py` to pick an overtime
    # rule; set by nothing in app/, scripts/ or tests/. Harmless today only
    # because `STATE_OVERTIME_RULES` is empty - the trap springs when somebody
    # registers a state rule and it silently never fires.
    "state_code": "A9: Hub.state_code selects an overtime rule and nothing sets it",
    # Nothing in app/ or scripts/ creates a Driver at all - a row can only be
    # made by hand. The default of 1 unit is at least the safe direction: a
    # hand-made driver is under-assigned rather than over-assigned.
    "vehicle_capacity_units": "A-series: no driver-creation path exists; default 1 is the safe direction",
    "stripe_connect_account_id": "A11: gig payout is stubbed - app/config.py says no driver has a real one",
    # Dead rather than dangerous: read by nothing either, so a flag simply does
    # not record who raised it.
    "created_by_driver_id": "R5: StopFlag does not record who raised it - read by nothing either",
    # M5's survey answers. `set_autonomy_fit` writes them and is itself an
    # allowlisted orphan: nothing asks a receiver yet.
    "curb_access": "M5: survey answer, nothing asks a receiver yet",
    "door_path": "M5: survey answer, nothing asks a receiver yet",
    "landing_surface": "M5: survey answer, nothing asks a receiver yet",
    "obstruction": "M5: survey answer, nothing asks a receiver yet",
    "who_receives": "M5: survey answer, nothing asks a receiver yet",
    # Stated decisions, both documented where they live.
    "cost_actuals_cents": "REC-2: costs go to the ledger instead, deliberately - see app/record/cost.py",
    "modality_eligible": "M5: carried now, used later - see the column's own comment",
    # Written by SQLAlchemy's `onupdate`, not by any statement in the tree.
    "updated_at": "TimestampMixin: written by onupdate, invisible to a syntax check",
    # Filtered on in five places; nothing sets it False because nothing
    # deactivates a hub or client yet. Read heavily, so not a silent default.
    "active": "B-series: no deactivation path yet - read in five places and defaults True",
}


def _mapped_columns() -> dict[str, set[str]]:
    """Every `mapped_column` attribute declared under `app/models/`."""
    columns: dict[str, set[str]] = {}
    for path in sorted(MODELS.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                    continue
                name = stmt.target.id
                if name.startswith("_") or stmt.value is None:
                    continue
                if "mapped_column" in ast.unparse(stmt.value):
                    columns.setdefault(name, set()).add(node.name)
    return columns


def _written_names() -> set[str]:
    """Attribute names assigned, or passed as a keyword, outside `app/models/`."""
    written: set[str] = set()
    sources = [
        p
        for p in list(APP.rglob("*.py")) + list((ROOT / "scripts").rglob("*.py"))
        if "__pycache__" not in str(p) and MODELS not in p.parents
    ]
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            targets: list = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Attribute):
                        written.add(sub.attr)
            if isinstance(node, ast.Call):
                written.update(kw.arg for kw in node.keywords if kw.arg)
    return written


def test_no_column_is_written_by_nothing():
    """A new name here means a column that will hold its default for ever.

    The fix is almost never to add it to `KNOWN_UNWRITTEN` - it is to write the
    column, or to notice the feature that reads it cannot work yet and say so.
    """
    columns = _mapped_columns()
    written = _written_names()
    unwritten = {
        name: models for name, models in columns.items() if name not in written
    }
    new = {n: m for n, m in unwritten.items() if n not in KNOWN_UNWRITTEN}

    assert not new, (
        "declared and never written:\n"
        + "\n".join(f"  {n}  ({', '.join(sorted(m))})" for n, m in sorted(new.items()))
        + "\n\nWrite it, or add it to KNOWN_UNWRITTEN with the reason."
    )


def test_the_allowlist_does_not_outlive_the_gap():
    """An entry that is written now has been fixed - remove it, or the list
    becomes a place names go to be forgotten."""
    columns = _mapped_columns()
    written = _written_names()
    stale = sorted(
        name
        for name in KNOWN_UNWRITTEN
        if name in columns and name in written
    )

    assert not stale, (
        "these are written now - remove them from KNOWN_UNWRITTEN:\n"
        + "\n".join(f"  {name}" for name in stale)
    )


def test_every_allowlisted_column_still_exists():
    """A column that has been deleted should leave the list with it."""
    columns = _mapped_columns()
    gone = sorted(name for name in KNOWN_UNWRITTEN if name not in columns)

    assert not gone, (
        "these columns no longer exist - remove them from KNOWN_UNWRITTEN: "
        + ", ".join(gone)
    )


@pytest.mark.parametrize("name,reason", sorted(KNOWN_UNWRITTEN.items()))
def test_every_entry_explains_itself(name, reason):
    """A reason of "unused" is a shrug. Each says which item it belongs to or
    why it cannot be written yet, so somebody can decide whether it matters."""
    assert len(reason) > 25, f"{name}'s reason is too thin to act on: {reason!r}"
    assert ":" in reason, f"{name}'s reason should name the item it belongs to"
