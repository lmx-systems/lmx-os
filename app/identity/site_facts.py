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

**An answer fills an open fact; it never overwrites a person's.** A fact is open
while it is unknown or only the desk's guess. An answer to a fact a person or
the import already gave is applied only when it agrees (and then changes
nothing); one that disagrees is kept beside the fact as a dispute, with who gave
it and when, for a person to settle (`site_fact_answers`, migration 0081). So a
phone replaying a stale offline answer cannot undo a newer one, and two drivers
who disagree leave both answers on the record rather than whichever arrived
last. A "no" to a shared address is a dispute, too, wherever the import recorded
the address as shared with another site.

**Every writer of a site fact records its source per fact** through
`set_site_fact` - the import, the desk and the survey. A fact with no entry is
one written before that rule, and has the site's `site_source`.

**A test-site draw is not moved by an answer here (D-TEST).** `site_draws` keeps
the group a site was drawn from (`app/record/site_draws.py`), and a site is drawn
once, ever, so correcting the kind or setting of a drawn site changes what the
site is now, never whether it is a test site or which block it was drawn in.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.resolution import canonical_location
from app.models.location import SETTINGS, SITE_FACTS, SITE_KINDS, Location
from app.models.site_address_share import SiteAddressShare

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


def fact_is_open(location: Location, name: str) -> bool:
    """Whether an answer may fill this fact: it is unknown, or only the desk's."""
    return getattr(location, name) is None or fact_source(location, name) == SOURCE_DESK


def facts_to_ask(location: Location) -> list[str]:
    """The site facts the door survey should ask: unknown, or only the desk's."""
    return [name for name in SITE_FACTS if fact_is_open(location, name)]


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


@dataclass
class SiteFactsAnswered:
    """What became of an answer: the site it was for, the facts it filled, and
    the ones it disagreed with and left as they were."""

    site: Location
    applied: list[str] = field(default_factory=list)
    disputed: list[str] = field(default_factory=list)


async def _lock_canonical(session: AsyncSession, location: Location) -> Location:
    """The site's row as it is now, locked: the dock it was merged into, looked
    up again if a merge landed between the lookup and the lock."""
    site = location
    for _ in range(4):
        canonical = await canonical_location(session, site)
        site = (
            await session.execute(
                select(Location)
                .where(Location.id == canonical.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if site.merged_into_id is None:
            return site
    return site


async def _recorded_as_shared(session: AsyncSession, site: Location) -> bool:
    """Whether the import recorded this site as sharing its address with another."""
    found = await session.scalar(
        select(SiteAddressShare.location_id)
        .where(
            or_(
                SiteAddressShare.location_id == site.id,
                SiteAddressShare.shares_with_location_id == site.id,
            )
        )
        .limit(1)
    )
    return found is not None


async def record_site_facts(
    session: AsyncSession,
    location: Location,
    *,
    kind_of_site: str | None = None,
    setting: str | None = None,
    shares_address: bool | None = None,
    by: uuid.UUID | None = None,
    stop_id: uuid.UUID | None = None,
) -> SiteFactsAnswered | None:
    """Write what the person at the door answered onto the site, as `survey`.

    None is "not answered" and leaves the fact alone. An answer fills a fact
    only while it is open (`fact_is_open`); one that disagrees with a person's
    or the import's is kept as a dispute with who gave it, and the fact is left
    as it was. The site row is taken last, after the caller's stop (the lock
    order in docs/ARCHITECTURE.md), and locked before it is read, as the desk
    locks it, so the two cannot interleave a read and a write. Returns None
    when nothing was answered. Flushes; the caller commits.
    """
    if kind_of_site is not None and kind_of_site not in SITE_KINDS:
        raise SiteFactRefused(f"kind_of_site must be one of {', '.join(SITE_KINDS)}")
    if setting is not None and setting not in SETTINGS:
        raise SiteFactRefused(f"setting must be one of {', '.join(SETTINGS)}")
    answers = {"kind_of_site": kind_of_site, "setting": setting, "shares_address": shares_address}
    if all(value is None for value in answers.values()):
        return None
    site = await _lock_canonical(session, location)
    outcome = SiteFactsAnswered(site=site)
    record = {name: dict(entry) for name, entry in (site.site_fact_answers or {}).items()}
    answer = {
        "by": str(by) if by is not None else None,
        "at": datetime.now(timezone.utc).isoformat(),
        "stop_id": str(stop_id) if stop_id is not None else None,
    }
    for name, value in answers.items():
        if value is None:
            continue
        contradicts_import = (
            name == "shares_address" and value is False and await _recorded_as_shared(session, site)
        )
        if fact_is_open(site, name) and not contradicts_import:
            set_site_fact(site, name, value, SOURCE_SURVEY)
            entry = record.get(name, {})
            record[name] = {**answer, "value": value, "disputes": list(entry.get("disputes", []))}
            outcome.applied.append(name)
        elif getattr(site, name) != value:
            entry = record.get(name, {})
            record[name] = {**entry, "disputes": [*entry.get("disputes", []), {**answer, "value": value}]}
            outcome.disputed.append(name)
        # An answer that agrees with a person's changes nothing.
    # A new dict, so the JSONB column is seen as changed.
    site.site_fact_answers = record
    await session.flush()
    return outcome
