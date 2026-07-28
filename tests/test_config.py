"""
Config defaults (docs/ROADMAP.md Decision log, July 2026). Pure unit test,
no DB/Redis.
"""
from app.config import Settings


def test_environment_defaults_to_production_fail_closed():
    # A forgotten ENVIRONMENT must resolve to the *strict* setting, so the
    # boot-time JWT-secret / Twilio-webhook guards fire rather than silently
    # allowing the repo's forgeable default secrets in a real deploy. Every
    # non-prod context opts in explicitly (`.env` -> development,
    # tests/conftest.py -> test), so this default only ever bites a deploy
    # that set nothing - which is exactly when it should.
    assert Settings.model_fields["environment"].default == "production"
