"""Every requirement is locked, the lock allows it, and the two locks agree.

CI, the Docker image and local venvs install the lock files, not the floors in
`requirements*.txt`. Before the locks existed every CI run took whatever was
newest that day, and on 30 Sep 2026 CI was a SQLAlchemy minor ahead of every
developer's venv - a mypy gate that passed locally failed there (#127).

A lock only helps while it matches its source. A requirement added to a floor
file but never re-locked is simply not installed, and the first sign would be an
ImportError in production. These tests are that sign, earlier.
"""
import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
PAIRS = [
    ("requirements.txt", "requirements.lock"),
    ("requirements-dev.txt", "requirements-dev.lock"),
]
_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;]+)")


def _requirements(path: str) -> list[Requirement]:
    """Direct requirements, following `-r` includes."""
    found: list[Requirement] = []
    for raw in (ROOT / path).read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            found += _requirements(line[3:].strip())
            continue
        found.append(Requirement(line))
    return found


def _pins(path: str) -> dict[str, Version]:
    pins: dict[str, Version] = {}
    for line in (ROOT / path).read_text().splitlines():
        match = _PIN.match(line)
        if match:
            pins[canonicalize_name(match.group(1))] = Version(match.group(2))
    return pins


@pytest.mark.parametrize("source, lock", PAIRS)
def test_every_requirement_is_locked_at_a_version_it_allows(source, lock):
    pins = _pins(lock)
    for requirement in _requirements(source):
        name = canonicalize_name(requirement.name)
        assert name in pins, (
            f"{requirement.name} is in {source} but not in {lock}: "
            f"re-run the command at the top of {lock}"
        )
        assert requirement.specifier.contains(pins[name], prereleases=True), (
            f"{lock} pins {requirement.name}=={pins[name]}, "
            f"which {source} does not allow ({requirement.specifier})"
        )


def test_the_image_runs_what_ci_tested():
    """The Docker image installs requirements.lock; CI tests with requirements-dev.lock.

    The image runs a tested set only if the two agree on every package they
    share - which they do when both are compiled together, and stop doing the
    moment one is regenerated without the other.
    """
    runtime, dev = _pins("requirements.lock"), _pins("requirements-dev.lock")
    disagree = {
        name: (str(runtime[name]), str(dev[name]))
        for name in runtime
        if name in dev and runtime[name] != dev[name]
    }
    assert not disagree, f"the locks disagree (runtime, dev): {disagree}"
    assert set(runtime) <= set(dev), "the runtime lock has packages the dev lock does not"
