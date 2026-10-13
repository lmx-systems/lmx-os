"""A site's three place facts, answered and confirmed at the door (`DE-15`, `DE-6`).

`docs/ROADMAP_1.5.md` §2.5 D-DESK. A record's end counts only when its site's
kind of business, setting and shared-address answer are known (`record_tiers`),
and a fact the desk filled from public data caps the record at bronze until a
person confirms it. On the first run over the New Jersey sites the shared-address
answer was the fact most often missing, and nothing at the door asked for it.

The door survey now asks, but only what is still open: a fact nobody knows, or
one only the desk has said. A fact a person or the import already gave is not
asked again - the survey is short on purpose, and every extra question is one a
driver skips. The answer is written with the source `survey`, **even when it is
the value already there**: confirming the desk's guess is exactly what lifts the
cap, and a confirmation that changed nothing in the row would change nothing in
the grade.

"Not sure" is no answer and writes nothing; unknown stays null.

**Every writer of a site fact records its source per fact** through
`set_site_fact` - the import, the desk and the survey. A fact with no entry is
one written before that rule, and has the site's `site_source`.

**A test-site draw is not moved by an answer here (D-TEST).** `site_draws` keeps
the group a site was drawn from (`app/record/site_draws.py`), and a site is drawn
once, ever, so correcting the kind or setting of a drawn site changes what the
site is now, never whether it is a test site or which block it was drawn in.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.resolution import canonical_location
from app.models.location import SETTINGS, SITE_FACTS, SITE_KINDS, Location

SOURCE_SURVEY = "survey"
SOURCE_DESK = "desk"


class SiteFactRefused(ValueError):
    """A kind or setting outside the vocabulary."""


def fact_source(location: Location, name: str) -> str | None:
    """Where this fact came from: its own entry, else the site's `site_source`."""
    entry = (location.site_fact_sources or {}).get(name)
    return entry if entry is not None else location.site_source


def set_site_fact(location: Location, name: str, value: object, source: str) -> None:
    """Write one site fact and its source together. The one way a fact is written."""
    if name not in SITE_FACTS:
        raise ValueError(f"not a site fact: {name}")
    # Attribute by attribute rather than setattr, so
    # tests/test_no_write_only_columns.py can see each column is written.
    if name == "kind_of_site":
        location.kind_of_site = value  # type: ignore[assignment]
    elif name == "setting":
        location.setting = value  # type: ignore[assignment]
    else:
        location.shares_address = value  # type: ignore[assignment]
    # A new dict, so the JSONB column is seen as changed.
    location.site_fact_sources = {**(location.site_fact_sources or {}), name: source}


def facts_to_ask(location: Location) -> list[str]:
    """The site facts the door survey should ask: unknown, or only the desk's."""
    return [
        name
        for name in SITE_FACTS
        if getattr(location, name) is None or fact_source(location, name) == SOURCE_DESK
    ]


async def site_facts_for(session: AsyncSession, location: Location) -> dict:
    """What the survey shows about the site: the three facts as known now, and
    which to ask. Read from the site as it is now - the dock it was merged into."""
    site = await canonical_location(session, location)
    return {
        "kind_of_site": site.kind_of_site,
        "setting": site.setting,
        "shares_address": site.shares_address,
        "ask": facts_to_ask(site),
    }


async def record_site_facts(
    session: AsyncSession,
    location: Location,
    *,
    kind_of_site: str | None = None,
    setting: str | None = None,
    shares_address: bool | None = None,
) -> Location | None:
    """Write what the person at the door answered onto the site, as `survey`.

    None is "not answered" and leaves the fact alone. The site row is locked
    first, as the desk locks it, so the two cannot interleave a read and a
    write. Returns the site written to, or None when nothing was answered.
    Flushes; the caller commits.
    """
    if kind_of_site is not None and kind_of_site not in SITE_KINDS:
        raise SiteFactRefused(f"kind_of_site must be one of {', '.join(SITE_KINDS)}")
    if setting is not None and setting not in SETTINGS:
        raise SiteFactRefused(f"setting must be one of {', '.join(SETTINGS)}")
    answers = {"kind_of_site": kind_of_site, "setting": setting, "shares_address": shares_address}
    if all(value is None for value in answers.values()):
        return None
    canonical = await canonical_location(session, location)
    site = (
        await session.execute(
            select(Location)
            .where(Location.id == canonical.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    for name, value in answers.items():
        if value is not None:
            set_site_fact(site, name, value, SOURCE_SURVEY)
    await session.flush()
    return site
