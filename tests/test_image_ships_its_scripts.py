"""The image carries every script the runbook runs in it.

infra/README.md creates the first ops user with a one-off `aws ecs run-task` of
the app image running `python -m scripts.create_ops_user`, and settlement,
signing, enrolment and rates are operational scripts by design
(OPERATIONAL_SCRIPTS in tests/test_no_new_orphans.py). The Dockerfile copied
`app`, `migrations` and `demo` but not `scripts`, so on a fresh stack nobody
could create a login, and the console would have read like a wrong password.
"""
import ast
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Operational, but needing more than an image can hold.
NEEDS_MORE_THAN_THE_IMAGE = {
    "import_inherited_dwell.py": (
        "reads ml/ and the previous operator's export, neither of which ships - "
        "run it where the export is"
    ),
}


def _copied() -> set[str]:
    """Top-level paths the Dockerfile copies into the image."""
    copied = set()
    for line in (ROOT / "Dockerfile").read_text().splitlines():
        match = re.match(r"\s*COPY\s+(?:--\S+\s+)*(\S+)\s+\S+\s*$", line)
        if match:
            copied.add(match.group(1).rstrip("/").split("/")[0])
    return copied


def _operational_scripts() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("_orphans", ROOT / "tests" / "test_no_new_orphans.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OPERATIONAL_SCRIPTS


def _first_party_imports(path: Path) -> set[str]:
    """Top-level packages a script imports that live in this repository."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {name for name in names if (ROOT / name).is_dir()}


def test_the_image_copies_scripts():
    assert "scripts" in _copied()


def test_every_operational_script_has_what_it_imports_in_the_image():
    copied = _copied()
    missing = {}
    for name in _operational_scripts():
        if name in NEEDS_MORE_THAN_THE_IMAGE:
            continue
        lacking = _first_party_imports(ROOT / "scripts" / name) - copied
        if lacking:
            missing[name] = sorted(lacking)
    assert not missing, f"operational scripts import what the image doesn't copy: {missing}"


def test_every_script_the_runbook_runs_is_in_the_image():
    readme = (ROOT / "infra" / "README.md").read_text()
    modules = set(re.findall(r'"python",\s*"-m",\s*"(scripts\.[a-z_0-9]+)"', readme))
    modules |= set(re.findall(r"python -m (scripts\.[a-z_0-9]+)", readme))
    assert modules, "the runbook names no script - this is reading the wrong file"
    copied = _copied()
    for module in sorted(modules):
        assert (ROOT / (module.replace(".", "/") + ".py")).exists(), f"{module} does not exist"
        assert module.split(".")[0] in copied, f"{module} is not in the image"


def test_the_exemptions_are_still_operational():
    """An exemption for a script that was renamed or retired is a hole."""
    assert set(NEEDS_MORE_THAN_THE_IMAGE) <= set(_operational_scripts())
