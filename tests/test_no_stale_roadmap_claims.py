"""A roadmap row cannot claim something the tree contradicts.

The other three invariants check the code against itself. This one checks the
**documents** against the code, because that is where this repository actually
goes wrong. In a single day of ordinary work, three status claims turned out to
be stale, and none of them was found by looking for stale claims:

* `DRV-1` read `NEW` ten days after it shipped as #39 and #41 — while `REC-2`'s
  row, three tables below it in the same file, said *"`DRV-1` shipped
  `stop_geofence_event`, and the old row predates it"*.
* `MODEL_AND_DATA_BRIEF.md` recorded gates `0.4` and `0.8` as **closed** in two
  places. Both are open, and `BACKGROUND_LOCATION_CONSENT.md` says outright
  that the document does not close them.
* `THE_DRIVER_APP.md` §5 listed `DRV-3` under *"what is not built"* a week
  after `bb8e101` built it.

`CLAUDE.md` already warns that a stale clone once made a full audit report
shipped work as unbuilt. The lesson it draws — *verify item status against the
code* — is a thing a person must remember. This is that read, automated.

## The two rules, and why not more

**Every path a document cites must exist.** 258 paths are cited across the
roadmap documents and a rename silently invalidates any of them. Resolved
against a short list of front-end roots as well as the repository root,
because rows abbreviate `driver-app/src/auth/token.ts` to `src/auth/token.ts`
and that is a reasonable thing to write in a row about the driver app.

**A `BUILT` row must name something a reader can open**, and an item claiming
`NEW` must have no commit named after it. The second rule is `DRV-1` exactly:
the commit subjects in this repository begin with the item id (`DRV-3: the
warehouse fence`, `W1: the ops half`), so a row saying `NEW` while
`git log` holds `DRV-1 (app): register the stop geofences` is a contradiction
no reader should have to notice.

**What is deliberately not checked: gate status in prose.** The
`MODEL_AND_DATA_BRIEF.md` failure is the one bug of the three this file cannot
catch, and the honest reason is that catching it means reading English.
`BACKGROUND_LOCATION_CONSENT.md` contains the sentence *"0.4 and 0.8 are not
closed by this document"* — a phrase-match for "0.4 ... closed" fires on the
one document that gets it right. `tests/test_no_unreachable_routes.py` learned
this the expensive way and wrote the rule down: **a check that cries wolf gets
allowlisted into uselessness.** A rule that is wrong in the safe direction and
catches two of three beats a rule that catches three and is distrusted.

## What it found on its first run

`AGT-4` was marked `BUILT` and cited nothing but `REC-1`, another roadmap id.
It was genuinely built — `app/record/explain.py` — and was the only `BUILT` row
in the table naming nothing a reader could open. Same shape as the other
invariants' first runs: the check fires on the person writing it.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent

ROADMAP_1_5 = ROOT / "docs" / "ROADMAP_1.5.md"

# Documents whose citations are load-bearing. Not every markdown file: a
# meeting note that mentions a path nobody maintains is not a broken promise,
# and adding it here would make the list longer and the failures less
# believable — the same argument `NOT_A_FEATURE` makes about /health.
CITING_DOCS = (
    "docs/ROADMAP_1.5.md",
    "docs/ROADMAP.md",
    "docs/ROADMAP_RECONCILIATION.md",
    "docs/MODEL_AND_DATA_BRIEF.md",
    "docs/THE_DRIVER_APP.md",
    "docs/THE_RECORD_AND_IDENTITY_LAYERS.md",
)

# A row about the driver app writes `src/auth/token.ts`, not
# `driver-app/src/auth/token.ts`, and it is right to. Resolution tries the
# repository root first so an unambiguous path always wins.
PATH_ROOTS = (
    "",
    "driver-app/",
    "dashboard/",
    "client-portal/",
    "driver-app/src/",
    "dashboard/src/",
    "client-portal/src/",
)

# Backticked tokens that look like a file rather than a sentence. The extension
# list is the point: `app/identity` without one is a directory reference and
# ambiguous, and guessing at it is how a check starts crying wolf.
CITED_PATH = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:py|ts|tsx|md|json|sql|sh|yml|yaml|tf))`"
)

# `| **DRV-1** | description | `STATUS` | notes |`
STATUS_ROW = re.compile(
    r"^\|\s*\*\*([A-Z]{3}-\d+)\*\*\s*\|([^|]*)\|\s*`([^`]+)`\s*\|(.*)$", re.M
)

ANY_BACKTICKED = re.compile(r"`([A-Za-z0-9_./-]+)`")

# Paths a document may cite that are deliberately absent from the tree.
# `lmx-dwell/` is the design partner's raw operational export and is gitignored
# (`CLAUDE.md`), so a checkout has the documents describing it and not the data.
ALLOWED_MISSING: dict[str, str] = {}

# Rows whose status may claim NEW despite a commit named for them, with the
# reason. Empty, and an entry should be argued for: the usual cause is a commit
# that began an item rather than finished it, which is what a status like
# `HARNESS BUILT` exists to say instead.
NEW_DESPITE_COMMITS: dict[str, str] = {}


def _resolves(candidate: str) -> bool:
    return any((ROOT / (root + candidate)).exists() for root in PATH_ROOTS)


def _known_symbols() -> set[str]:
    """Every name a row could reasonably cite instead of a path.

    Terse rows name a symbol rather than a file — `DRV-6` says *"`FlagIssueScreen`
    exists"* and that is a better sentence than its path would be. Requiring a
    path would have failed eight rows that are all perfectly honest, which is
    the cry-wolf failure this file is trying not to repeat.
    """
    # Walked from the source roots, not from the repository root. `rglob("*")`
    # over the whole tree descends into `.venv` and `node_modules` before any
    # filter can reject them, which cost four of the five seconds this check
    # first took. `lmx-dwell/` is excluded by omission — it is the design
    # partner's gitignored export and holds no symbols anybody cites.
    source_roots = (
        "app",
        "ml",
        "scripts",
        "migrations",
        "tests",
        "demo",
        "dashboard/src",
        "client-portal/src",
        "driver-app/src",
        "infra",
        "docker",
    )
    skip = {"node_modules", "__pycache__", "dist", "build", ".expo"}
    symbols: set[str] = set()
    for root in source_roots:
        base = ROOT / root
        if not base.exists():  # pragma: no cover - all are in the repo
            continue
        for path in base.rglob("*"):
            if any(part in skip for part in path.parts):
                continue
            if not path.is_file():
                continue
            symbols.add(path.stem)
            if path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):  # pragma: no cover - binary stray
                continue
            symbols |= set(re.findall(r"^(?:async )?def (\w+)", text, re.M))
            symbols |= set(re.findall(r"^class (\w+)", text, re.M))
            symbols |= set(re.findall(r"^export (?:async )?function (\w+)", text, re.M))
            symbols |= set(re.findall(r"^export const (\w+)", text, re.M))
            # Table names. `CON-2` evidences itself with `dispatcher_overrides`
            # and a migration number, which is the right way to cite a schema
            # change and matches no function anywhere. It was passing only
            # because the old whole-tree walk swept 60,000 names out of
            # `.venv`, one of which collided — the narrower walk turned a false
            # pass into a false failure and showed the resolver was the problem.
            symbols |= set(re.findall(r'__tablename__\s*=\s*"(\w+)"', text))
            # Migration ids: `0060` is cited far more readably than
            # `migrations/versions/0060_dispatcher_overrides.py`.
            if path.parent.name == "versions":
                symbols.add(path.stem.split("_", 1)[0])
    return symbols


def _commit_subjects() -> list[str] | None:
    """Every commit subject, or None when history is not available.

    A shallow clone is the normal CI case and has no history to read. Returning
    None so the caller skips is the honest behaviour: the alternative is a rule
    that passes for the wrong reason on the machine that matters most, which is
    worse than one that says it did not run.
    """
    try:
        done = subprocess.run(
            ["git", "log", "--format=%s", "--all"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    if done.returncode != 0:
        return None
    subjects = [line for line in done.stdout.split("\n") if line.strip()]
    # One commit is a shallow clone or a fresh repository. Either way there is
    # nothing to contradict a row with.
    return subjects if len(subjects) > 1 else None


def test_every_cited_path_exists():
    """A document may not point at a file that is not there.

    The failure this prevents is quiet: a module gets renamed, the row that
    justified it keeps its old name, and the next person to check whether
    something was built finds nothing and concludes it was not.
    """
    missing: list[str] = []
    for doc in CITING_DOCS:
        path = ROOT / doc
        if not path.exists():  # pragma: no cover - all six are in the repo
            continue
        for candidate in CITED_PATH.findall(path.read_text()):
            # A bare filename has no directory to resolve against and would
            # match anything with that name anywhere. Skipped rather than
            # guessed at.
            if "/" not in candidate:
                continue
            if candidate in ALLOWED_MISSING or _resolves(candidate):
                continue
            missing.append(f"{doc}: {candidate}")

    assert not missing, (
        "these documents cite files that do not exist:\n      "
        + "\n      ".join(sorted(missing))
        + "\n\n    Fix the path, or add it to ALLOWED_MISSING with the reason it is absent."
    )


def test_every_built_row_names_something_real():
    """`BUILT` has to point at something a reader can open.

    `CLAUDE.md`'s own warning is that `BUILT` in this repository has meant
    *written, tested, and called by nothing*. The weaker version of that — a row
    asserting a thing exists and offering no way to check — is what this
    catches. It is not proof the item works; it is proof somebody can go and
    look, which the reader of a status table is entitled to.
    """
    symbols = _known_symbols()
    unevidenced: list[str] = []
    for item, _description, status, notes in STATUS_ROW.findall(ROADMAP_1_5.read_text()):
        if "BUILT" not in status.upper():
            continue
        tokens = ANY_BACKTICKED.findall(notes)
        # A roadmap id is not evidence. `AGT-4` cited `REC-1` and nothing else,
        # which is a cross-reference to another claim rather than to any code.
        tokens = [t for t in tokens if not re.fullmatch(r"[A-Z]{3}-\d+", t)]
        if any(("/" in t and _resolves(t)) or t in symbols for t in tokens):
            continue
        unevidenced.append(f"{item} [{status}]")

    assert not unevidenced, (
        "these rows claim BUILT and name no file or symbol that exists:\n      "
        + "\n      ".join(sorted(unevidenced))
        + "\n\n    Cite the module, function or screen — or the status is wrong."
    )


def test_no_row_claims_new_for_work_that_has_landed():
    """`DRV-1`, exactly.

    Commit subjects here start with the item id, so a row reading `NEW` beside
    `DRV-1 (app): register the stop geofences, queue every crossing (#41)` is a
    contradiction. Matched on a word boundary so `DRV-1` does not claim
    `DRV-10`'s commits.

    Deliberately one-directional. A commit named for an item does not prove the
    item is finished — half of `AGT-1` is built and its status says so — so this
    only fires on the status that asserts nothing has happened at all.
    """
    subjects = _commit_subjects()
    if subjects is None:
        import pytest

        pytest.skip("no git history available (shallow clone) - nothing to compare against")

    contradictions: list[str] = []
    for item, _description, status, _notes in STATUS_ROW.findall(ROADMAP_1_5.read_text()):
        if status.strip().upper() != "NEW":
            continue
        if item in NEW_DESPITE_COMMITS:
            continue
        named = [s for s in subjects if re.match(rf"{re.escape(item)}\b", s)]
        if named:
            contradictions.append(f"{item} says NEW, but: {named[0]}")

    assert not contradictions, (
        "these rows claim NEW for work that has commits:\n      "
        + "\n      ".join(sorted(contradictions))
        + "\n\n    Update the status, or add the item to NEW_DESPITE_COMMITS with why."
    )


def test_the_status_table_is_still_parseable():
    """The checks above are worth nothing if the rows stop matching.

    Same argument as `test_every_router_is_classified`: a reformat that made
    `STATUS_ROW` match nothing would turn all three assertions green and mean
    the opposite. 64 is the count as of v1.3; the floor moves up, never down,
    and a genuine reduction should be an argued edit rather than a silent one.
    """
    rows = STATUS_ROW.findall(ROADMAP_1_5.read_text())
    assert len(rows) >= 64, (
        f"only {len(rows)} status rows parsed out of ROADMAP_1.5.md, expected at least 64. "
        "Either rows were removed, or the table format changed and these checks "
        "are now inspecting nothing."
    )
