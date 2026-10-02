"""The scripts that cut a month must cut it the same way.

`settle_month.py` issues a statement's basis for a month, and `sign_basis.py`
finds that basis again by its exact bounds. When settlement moved to the hub's
month and signing stayed on UTC's, nothing could be signed - and an unsigned
basis is one settle_month refuses to write a PDF on. This holds them together.
"""
import importlib.util
import pathlib

from app.models.hub import Hub

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _month_bounds(script: str):
    spec = importlib.util.spec_from_file_location(f"_{script}", SCRIPTS / f"{script}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._month_bounds


def test_signing_looks_for_the_month_settlement_issued():
    hub = Hub(name="Hub", lat=34.05, lng=-118.24, timezone="America/Los_Angeles")
    for month in ("2026-08", "2026-11", "2026-12"):
        assert _month_bounds("sign_basis")(month, hub) == _month_bounds("settle_month")(month, hub)
