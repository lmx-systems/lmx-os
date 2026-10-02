"""The timers docs/ALERTING.md calls for exist in the Terraform, and match the app.

Three /internal endpoints have to be called on a schedule. The document said so
while nothing in infra/aws/ did it, so a failed webhook was never retried and
the retention periods the privacy policy states were never enforced. These tie
three things together - the sweeps the document lists, the routes the app
serves, and the schedules Terraform creates - and check the connection sends
the header the app actually reads.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEDULES = (ROOT / "infra" / "aws" / "schedules.tf").read_text()


def _documented_sweeps() -> set[str]:
    text = (ROOT / "docs" / "ALERTING.md").read_text()
    section = text.split("### 6. The three scheduled jobs", 1)[1].split("\n### ", 1)[0]
    return set(re.findall(r"`POST (/internal/[a-z/-]+)`", section))


def _scheduled_paths() -> set[str]:
    return set(re.findall(r'path\s*=\s*"(/internal/[^"]+)"', SCHEDULES))


def test_the_document_names_three_sweeps():
    assert len(_documented_sweeps()) == 3


def test_every_documented_sweep_is_scheduled():
    assert _documented_sweeps() <= _scheduled_paths()


def test_every_scheduled_path_is_a_route_the_app_serves():
    # The schema rather than app.routes: FastAPI keeps an included router as one
    # nested entry there, so the flat list doesn't show its paths.
    from app.main import app

    paths = app.openapi()["paths"]
    missing = {path for path in _scheduled_paths() if "post" not in paths.get(path, {})}
    assert not missing, f"scheduled but not served: {sorted(missing)}"


def test_the_connection_sends_the_header_the_app_reads():
    internal = (ROOT / "app" / "api" / "internal_routes.py").read_text()
    param = re.search(r"def require_internal_secret\(\s*(\w+): str \| None = Header", internal)
    assert param, "require_internal_secret changed shape - re-read how it takes the token"
    sent = re.search(r'key\s*=\s*"([^"]+)"', SCHEDULES)
    assert sent
    assert sent.group(1).lower() == param.group(1).replace("_", "-").lower()


def test_the_token_is_generated_and_shared():
    secrets = (ROOT / "infra" / "aws" / "secrets.tf").read_text()
    assert re.search(r"INTERNAL_API_TOKEN\s*=\s*random_password\.internal_api_token\.result", secrets)
    assert "random_password.internal_api_token.result" in SCHEDULES


def test_links_point_at_the_portal_rather_than_localhost():
    ecs = (ROOT / "infra" / "aws" / "ecs.tf").read_text()
    assert re.search(r'name = "PORTAL_BASE_URL", value = "https://', ecs)
