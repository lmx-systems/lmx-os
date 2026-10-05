"""The console's rate form sends what it shows, for the tiers the API prices.

Two defects in one form:

  - it sent `rate_per_weight_unit_cents: 0` whatever the card said, so editing
    any other price on a tier wiped its per-weight price;
  - it offered T1 to T4. The API prices HOT_SHOT, T1, T2 and T3, so HOT_SHOT
    could never be priced from the console, and setting T4 stored a rate no
    order could match.

Both are read from the front ends' source, as the CORS test reads their
methods, since the console has no test runner of its own.
"""
import re
from pathlib import Path

import pytest

from app.api.admin_routes import VALID_SLA_TIERS
from app.schemas.admin import ClientRateBody

ROOT = Path(__file__).resolve().parent.parent
FRONT_ENDS = ("dashboard/src", "client-portal/src")
RATE_FORM = ROOT / "dashboard/src/components/ClientRatesPanel.tsx"

# A tier list is a constant array of string literals whose name ends in TIERS.
TIER_LIST = re.compile(r"const\s+\w*TIERS\s*=\s*\[([^\]]*)\]")


def _tier_lists() -> dict[str, set[str]]:
    found = {}
    for folder in FRONT_ENDS:
        for path in sorted((ROOT / folder).rglob("*.ts*")):
            source = path.read_text()
            for match in TIER_LIST.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                # "all" is a filter's own option, not a tier.
                tiers = set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))) - {"all"}
                found[f"{path.relative_to(ROOT)}:{line}"] = tiers
    return found


def test_the_scan_finds_the_tier_lists():
    """If this fails the scan is reading the wrong thing, not the code."""
    lists = _tier_lists()
    assert any("ClientRatesPanel" in where for where in lists)
    assert len(lists) >= 5


@pytest.mark.parametrize("where", sorted(_tier_lists()))
def test_a_tier_list_offers_the_tiers_the_api_prices(where):
    assert _tier_lists()[where] == VALID_SLA_TIERS


def test_a_rate_edit_sends_every_price_from_the_form():
    body = re.search(r"api\.upsertClientRate\(clientId, \{(.*?)\n\s*\}\)", RATE_FORM.read_text(), re.S)
    assert body, "the scan is reading the wrong thing"
    sent = dict(re.findall(r"^\s*(\w+):\s*(.+?),?$", body.group(1), re.M))

    for price in set(ClientRateBody.model_fields) - {"sla_tier"}:
        assert price in sent, f"{price} isn't sent"
        assert "draft." in sent[price], f"{price} is sent as {sent[price]!r}, not from the form"
