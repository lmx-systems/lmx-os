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

**One answer per stop, and a retry is a repeat, never a second answer.** An
answer carries the stop it was given at, and only a drop-off, a visit or an
off-platform counter that is finished (`completed`, or a logged record's
`recorded`) may give one (`stop_refusal`). A request repeating what that stop
already answered changes nothing - so an outbox retry after a lost response
leaves one dispute, not two - and one contradicting it is refused
(`AlreadyAnsweredHere`). A fact keeps at most `MAX_DISPUTES` open disputes,
oldest dropped.

**A person settles a dispute** (`settle_dispute`, the console's "Answers that
disagree"): keep the fact as it is or take the disputed value, either way as
`ops`, with who settled and when. The settled disputes stay in the fact's
history, so a late retry of one is still recognised as a repeat.

**Every writer of a site fact records its source per fact** through
`set_site_fact` - the import, the desk and the survey. A fact with no entry is
one written before that rule, and has the site's `site_source`.

**A person's "keep" is a decision, not a deferral.** When a settlement keeps the
fact as it was, the values it turned down are not raised again: the same answer
from another stop is remembered (`overruled`) but makes no new dispute. Without
this, keeping a shared address "unknown" against the import's pair re-raised
every later door's "no". A different value still makes a dispute, and a
settlement that takes a value lifts nothing it did not turn down.

**A merge carries the answers to the dock it merges into** (`fold_answers`,
called by `app/identity/merge.py`). An open dispute, or an applied answer that
disagrees with the surviving dock's fact, becomes a dispute there; every stop's
answer is remembered there (`absorbed`), so a retry from one of the merged
dock's stops is still a repeat. Each carried entry names the dock it came from,
and undoing the merge takes back exactly those still waiting (`unfold_answers`).
The merged dock keeps its own record untouched, so it comes back as it was. The
facts themselves are not carried: a surviving dock with an open fact is asked at
its next door.

**A test-site draw is not moved by an answer here (D-TEST).** `site_draws` keeps
the group a site was drawn from (`app/record/site_draws.py`), and a site is drawn
once, ever, so correcting the kind or setting of a drawn site changes what the
site is now, never whether it is a test site or which block it was drawn in.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.resolution import canonical_location
from app.models.location import SETTINGS, SITE_FACTS, SITE_KINDS, Location
from app.models.site_address_share import SiteAddressShare
from app.models.stop import Stop

SOURCE_SURVEY = "survey"
SOURCE_DESK = "desk"
SOURCE_OPS = "ops"

# Open disputes kept per fact, and settlements kept in its history. A dispute is
# a second opinion for a person to weigh; twenty of them is a site somebody
# should visit, not a longer list.
MAX_DISPUTES = 20
MAX_SETTLED = 20
# Remembered answers a fact carries only so a retry is known as a repeat: those
# a merge brought in, and those a settlement had already turned down.
MAX_REMEMBERED = 100

# The stops that may answer about a site, and when: the door of a drop-off or a
# visit, or an off-platform record's counter (`DE-7`), once the stop is done.
ANSWERING_STOP_STATUSES = ("completed", "recorded")


class SiteFactRefused(ValueError):
    """A kind or setting outside the vocabulary."""


class AlreadyAnsweredHere(ValueError):
    """This stop already answered the fact differently. One answer per stop."""


class NewerAnswer(ValueError):
    """A dispute arrived after the one the person settling last saw."""


def stop_refusal(stop: Stop) -> str | None:
    """Why this stop may not answer about its site, or None when it may.

    A pickup at a distributor's counter is not the delivery door, and a stop
    the driver never finished - pending, failed, cancelled - is a door they may
    never have stood at. Only an off-platform pickup names its own counter.
    """
    is_counter = stop.stop_type == "pickup" and stop.record_source == "off_platform"
    if stop.stop_type not in ("dropoff", "visit") and not is_counter:
        return "Only a delivery door, a visit or a logged counter can answer about its site"
    if stop.status not in ANSWERING_STOP_STATUSES:
        return "This stop is not finished, so it cannot answer about its site yet"
    return None


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


def validate_site_facts(*, kind_of_site: str | None, setting: str | None) -> None:
    """Refuse a kind or setting outside the vocabulary (`SiteFactRefused`)."""
    if kind_of_site is not None and kind_of_site not in SITE_KINDS:
        raise SiteFactRefused(f"kind_of_site must be one of {', '.join(SITE_KINDS)}")
    if setting is not None and setting not in SETTINGS:
        raise SiteFactRefused(f"setting must be one of {', '.join(SETTINGS)}")


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
    validate_site_facts(kind_of_site=kind_of_site, setting=setting)
    answers = {"kind_of_site": kind_of_site, "setting": setting, "shares_address": shares_address}
    if all(value is None for value in answers.values()):
        return None
    site = await _lock_canonical(session, location)
    outcome = SiteFactsAnswered(site=site)
    record = {name: dict(entry) for name, entry in (site.site_fact_answers or {}).items()}
    if stop_id is not None:
        # A repeat of what this stop already said is a retry: nothing to do. A
        # different answer from the same stop is refused before anything is
        # written, so the request is all or nothing.
        for name, value in answers.items():
            earlier = _answered_at(record.get(name, {}), stop_id)
            if value is not None and earlier is not _UNANSWERED and earlier != value:
                raise AlreadyAnsweredHere(f"This stop already answered {name}")
    answer = {
        "by": str(by) if by is not None else None,
        "at": datetime.now(timezone.utc).isoformat(),
        "stop_id": str(stop_id) if stop_id is not None else None,
    }
    for name, value in answers.items():
        if value is None:
            continue
        entry = record.get(name, {})
        if stop_id is not None and _answered_at(entry, stop_id) is not _UNANSWERED:
            continue
        contradicts_import = (
            name == "shares_address" and value is False and await _recorded_as_shared(session, site)
        )
        if fact_is_open(site, name) and not contradicts_import:
            set_site_fact(site, name, value, SOURCE_SURVEY)
            record[name] = {**entry, **answer, "value": value, "disputes": list(entry.get("disputes", []))}
            outcome.applied.append(name)
        elif getattr(site, name) != value and value in _turned_down(entry):
            # A person already kept the fact against this very value; another
            # door saying it again is remembered, not raised again.
            overruled = [*entry.get("overruled", []), {**answer, "value": value}][-MAX_REMEMBERED:]
            record[name] = {**entry, "overruled": overruled}
        elif getattr(site, name) != value:
            disputes = [*entry.get("disputes", []), {**answer, "value": value}][-MAX_DISPUTES:]
            record[name] = {**entry, "disputes": disputes}
            outcome.disputed.append(name)
        elif stop_id is not None:
            # Agrees with a person's: changes nothing, but remembered as this
            # stop's answer so a contradicting one from it is refused.
            agreed = [*entry.get("agreed", []), {**answer, "value": value}][-MAX_DISPUTES:]
            record[name] = {**entry, "agreed": agreed}
    # A new dict, so the JSONB column is seen as changed.
    site.site_fact_answers = record
    await session.flush()
    return outcome


_UNANSWERED = object()


def _turned_down(entry: dict) -> list:
    """The values the fact's latest settlement kept it against, if it kept it."""
    settled = entry.get("settled") or []
    if not settled or not settled[-1].get("kept"):
        return []
    return [d.get("value") for d in settled[-1].get("disputes", [])]


def _answered_at(entry: dict, stop_id: uuid.UUID) -> object:
    """What this stop already answered for one fact, or `_UNANSWERED`: as the
    applied answer, an open dispute, an agreement, or a settled dispute."""
    wanted = str(stop_id)
    if entry.get("stop_id") == wanted:
        return entry.get("value")
    given = [
        *entry.get("disputes", []),
        *entry.get("agreed", []),
        *entry.get("overruled", []),
        *entry.get("absorbed", []),
        *(d for settled in entry.get("settled", []) for d in settled.get("disputes", [])),
        *(settled["replaced"] for settled in entry.get("settled", []) if settled.get("replaced")),
    ]
    for earlier in given:
        if earlier.get("stop_id") == wanted:
            return earlier.get("value")
    return _UNANSWERED


def dispute_key(answer: dict) -> str:
    """One answer's fingerprint, stable for as long as the answer exists: the stop
    that gave it, who, when and what. A merge carries an answer over unchanged in
    all four, so the same answer has the same key on the site it lands on."""
    return "|".join(
        [str(answer.get("stop_id") or ""), str(answer.get("by") or ""), str(answer.get("at") or ""),
         json.dumps(answer.get("value"))]
    )


@dataclass
class OpenDispute:
    """One fact at one site where the people at the door disagreed."""

    location: Location
    fact: str
    current: str | bool | None
    current_source: str | None
    disputes: list[dict]


def open_disputes(location: Location) -> list[OpenDispute]:
    """The facts at this site with answers waiting for a person to settle."""
    return [
        OpenDispute(
            location=location,
            fact=name,
            current=getattr(location, name),
            current_source=fact_source(location, name),
            disputes=list(entry.get("disputes", [])),
        )
        for name, entry in (location.site_fact_answers or {}).items()
        if name in SITE_FACTS and entry.get("disputes")
    ]


class NothingToSettle(ValueError):
    """No open dispute for that fact, or a value nobody gave."""


async def settle_dispute(
    session: AsyncSession,
    location: Location,
    fact: str,
    *,
    take: object | None,
    by: uuid.UUID,
    seen: list[str],
) -> Location:
    """Settle a fact's open disputes: keep it as it is (`take` None) or take one
    of the disputed values. Either way the fact is now `ops` - a person looked -
    and the disputes move to its history with who settled them and when.

    `seen` is the `dispute_key` of every answer the person was shown. Settling
    files every open dispute as decided, so unless the open ones are exactly
    those, the settle is refused (`NewerAnswer`) and they look again. A set
    rather than the newest time shown, because a merge carries a dispute over
    with the time it was given at its own door - older than anything shown, and
    still never seen. Flushes; the caller commits."""
    if fact not in SITE_FACTS:
        raise NothingToSettle(f"not a site fact: {fact}")
    site = await _lock_canonical(session, location)
    record = {name: dict(entry) for name, entry in (site.site_fact_answers or {}).items()}
    entry = record.get(fact, {})
    disputes = list(entry.get("disputes", []))
    if not disputes:
        raise NothingToSettle("Nothing to settle: no answer disagrees with this fact")
    if {dispute_key(d) for d in disputes} != set(seen):
        raise NewerAnswer("The answers here have changed since this was loaded - look again")
    if take is not None and take not in [d.get("value") for d in disputes]:
        raise NothingToSettle("That value is not one the disputed answers gave")
    value = getattr(site, fact) if take is None else take
    set_site_fact(site, fact, value, SOURCE_OPS)
    now = datetime.now(timezone.utc).isoformat()
    settled = [
        *entry.get("settled", []),
        {
            "by": str(by), "at": now, "kept": take is None, "value": value, "disputes": disputes,
            # The answer a taken value replaces, so a late retry of it from its
            # stop is still known as a repeat.
            "replaced": None if take is None else {k: entry.get(k) for k in ("by", "at", "stop_id", "value")},
        },
    ][-MAX_SETTLED:]
    record[fact] = {**entry, "disputes": [], "settled": settled}
    if take is not None:
        record[fact].update({"by": str(by), "at": now, "stop_id": None, "value": value})
    site.site_fact_answers = record
    await session.flush()
    return site


def _remembered(entry: dict) -> list[dict]:
    """Every answer a fact's record holds from a stop, in the shape a repeat is
    checked against: the applied one, disputes, agreements, overruled, settled
    and absorbed."""
    given: list[dict] = []
    if entry.get("stop_id") is not None:
        given.append({k: entry.get(k) for k in ("by", "at", "stop_id", "value")})
    given += [
        *entry.get("disputes", []),
        *entry.get("agreed", []),
        *entry.get("overruled", []),
        *entry.get("absorbed", []),
        *(d for settled in entry.get("settled", []) for d in settled.get("disputes", [])),
        *(settled["replaced"] for settled in entry.get("settled", []) if settled.get("replaced")),
    ]
    return [{k: g.get(k) for k in ("by", "at", "stop_id", "value")} for g in given if g.get("stop_id")]


def fold_answers(source: Location, target: Location) -> None:
    """Carry a merged dock's answers to the dock it merges into. Both rows locked
    by the caller. The source's own record is left as it was, for a revert.

    Every stop's answer is remembered on the target (`absorbed`), so a retry from
    one of the source's stops is a repeat there too. An open dispute, and an
    applied answer the target's fact disagrees with, become disputes on the
    target for a person to settle - two docks just found to be one place, whose
    doors said different things. Each carried entry names the source
    (`from_location`), which is what `unfold_answers` takes back.
    """
    tag = {"from_location": str(source.id)}
    record = {name: dict(entry) for name, entry in (target.site_fact_answers or {}).items()}
    for name, entry in (source.site_fact_answers or {}).items():
        if name not in SITE_FACTS:
            continue
        current = getattr(target, name)
        raise_here = [
            {**d, **tag}
            for d in entry.get("disputes", [])
            if d.get("value") != current
        ]
        if entry.get("stop_id") is not None and entry.get("value") != current:
            raise_here.append({**{k: entry.get(k) for k in ("by", "at", "stop_id", "value")}, **tag})
        remembered = [{**g, **tag} for g in _remembered(entry)]
        if not raise_here and not remembered:
            continue
        mine = dict(record.get(name, {}))
        if raise_here:
            mine["disputes"] = [*mine.get("disputes", []), *raise_here][-MAX_DISPUTES:]
        mine["absorbed"] = [*mine.get("absorbed", []), *remembered][-MAX_REMEMBERED:]
        record[name] = mine
    # A new dict, so the JSONB column is seen as changed.
    target.site_fact_answers = record


def unfold_answers(source_id: uuid.UUID, target: Location) -> None:
    """Take back what `fold_answers` carried from this source and is still
    waiting: its disputes not yet settled, and the answers it remembered. A
    dispute a person already settled on the target stays in the target's
    history - that decision was made there."""
    wanted = str(source_id)
    record = {}
    for name, entry in (target.site_fact_answers or {}).items():
        mine = dict(entry)
        for key in ("disputes", "absorbed"):
            if key in mine:
                mine[key] = [e for e in mine[key] if e.get("from_location") != wanted]
        record[name] = mine
    target.site_fact_answers = record


async def lock_sites(session: AsyncSession, location_ids: list[uuid.UUID]) -> dict[uuid.UUID, Location]:
    """Lock these sites' rows in id order, read fresh. In id order so two writers
    holding several sites queue rather than deadlock: a merge and its revert take
    both sites here before any shop, and the desk's run (`apply_desk_facts`)
    takes its sites one by one in the same id order."""
    rows = (
        await session.scalars(
            select(Location)
            .where(Location.id.in_(location_ids))
            .order_by(Location.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    return {row.id: row for row in rows}
