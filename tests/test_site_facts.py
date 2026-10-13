"""Which site facts the door survey asks (`app/identity/site_facts.py`), offline."""
import pytest

from app.identity.site_facts import fact_source, facts_to_ask, set_site_fact
from app.models.location import Location


def _site(**facts) -> Location:
    return Location(normalized_address="x", address="x", **facts)


def test_unknown_facts_are_asked():
    assert facts_to_ask(_site()) == ["kind_of_site", "setting", "shares_address"]


def test_a_desk_guess_is_asked_and_anything_else_is_not():
    site = _site(
        kind_of_site="body_shop", setting="suburb", shares_address=True, site_source="import",
        site_fact_sources={"setting": "desk"},
    )
    assert facts_to_ask(site) == ["setting"]


def test_a_fact_with_no_entry_has_the_sites_source():
    site = _site(kind_of_site="body_shop", site_source="desk", site_fact_sources={"setting": "survey"})
    assert (fact_source(site, "kind_of_site"), fact_source(site, "setting")) == ("desk", "survey")


def test_writing_a_fact_writes_its_source_even_when_the_value_is_unchanged():
    site = _site(setting="suburb", site_fact_sources={"setting": "desk", "kind_of_site": "import"})
    set_site_fact(site, "setting", "suburb", "survey")
    assert site.site_fact_sources == {"setting": "survey", "kind_of_site": "import"}


def test_only_the_three_site_facts_can_be_written():
    with pytest.raises(ValueError):
        set_site_fact(_site(), "region", "TX", "survey")
