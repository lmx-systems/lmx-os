"""Every package app/ imports is installed by the lock the image installs.

boto3 was imported inside two functions - the Secrets Manager provider and the
S3 clients - and declared nowhere. Nothing failed until infra/aws set
SECRETS_MANAGER_SECRET_ID, and from then on the API, the migration task and
every script would have stopped at startup with ModuleNotFoundError. A lazy
import is invisible to anything that only imports modules, so this reads the
source instead.

The Dockerfile installs requirements.lock, not the dev lock, so a package that
is present here only as a test dependency does not count.
"""
import ast
import importlib.metadata
import re
import sys
from pathlib import Path

from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent


def _imported_modules() -> dict[str, set[str]]:
    """Top-level third-party modules imported anywhere under app/, with where."""
    found: dict[str, set[str]] = {}
    for path in sorted((ROOT / "app").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name in sys.stdlib_module_names or name in {"app", "__future__"}:
                    continue
                found.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    return found


def _production_lock() -> set[str]:
    text = (ROOT / "requirements.lock").read_text()
    return {
        canonicalize_name(match.group(1))
        for match in re.finditer(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", text, re.MULTILINE)
    }


def test_every_package_app_imports_is_in_the_production_lock():
    distributions = importlib.metadata.packages_distributions()
    locked = _production_lock()
    problems = []
    for module, files in sorted(_imported_modules().items()):
        where = ", ".join(sorted(files)[:2])
        provided_by = distributions.get(module)
        if not provided_by:
            problems.append(f"{module}: imported by {where}, and nothing installed here provides it")
        elif not any(canonicalize_name(dist) in locked for dist in provided_by):
            problems.append(
                f"{module}: imported by {where}, provided by {sorted(provided_by)}, "
                "none of which requirements.lock installs"
            )
    assert not problems, (
        "Imported by app/ but not installed by the image's lock - add it to "
        "requirements.txt and recompile:\n  " + "\n  ".join(problems)
    )


def test_the_scan_sees_lazy_imports():
    """The import that started this was inside a method. Make sure the walk
    reaches function bodies, or the check above proves nothing."""
    assert "boto3" in _imported_modules()
