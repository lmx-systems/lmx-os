"""Draw the frozen test sites, once per site and before collection (`DE-8`).

D-TEST: per group - kind of site x setting - test sites are chosen at random
**before** anything is collected there, and are never trained on. The table is
`test_site_draws` (`app/models/site_draw.py`).

**A permuted block per group, as `EXP-2` draws the control arm.** Within each run
of `round(1 / share)` sites in a group, in the order they are drawn, exactly one
is a test site, and which one is chosen by hashing the region's salt with the
group and the block number. Independent coin flips would give the right share
across a region and leave a small group with no test site, or all of them; the
block gives every group its share as nearly as its size allows. A group smaller
than one block still has each of its sites at exactly the share's chance.

**Drawn once.** A site that has been drawn is never drawn again, test or not, so
running the draw after new sites arrive extends each group's sequence of blocks
rather than reshuffling it. That is also why a region's share cannot change: the
blocks already laid down were cut for it.

**A site is drawn only when its group is known.** Kind of site and setting both
come from desk work before any visit; a site missing either is reported and left
for a later draw rather than being put in a group it might not belong to.

**Never New Jersey** (D-NJ). Refused here, and by a check constraint.
"""
from __future__ import annotations

import hashlib
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location
from app.models.site_draw import SiteDraw

NEVER_TESTED = frozenset({"NJ"})


class DrawRefused(ValueError):
    """The draw would break D-TEST or D-NJ, or contradict an earlier draw."""


@dataclass
class DrawReport:
    region: str
    share: float
    drawn: int = 0
    test: int = 0
    by_group: Counter = field(default_factory=Counter)
    test_by_group: Counter = field(default_factory=Counter)
    already_drawn: int = 0
    waiting_for_group: int = 0


def block_size(share: float) -> int:
    return max(1, round(1 / share))


def _test_position(salt: str, region: str, group: tuple[str, str], block: int, size: int) -> int:
    digest = hashlib.sha256(f"{salt}:{region}:{group[0]}:{group[1]}:{block}".encode()).hexdigest()
    return int(digest, 16) % size


async def draw_test_sites(
    session: AsyncSession, *, region: str, share: float, drawn_by: str
) -> DrawReport:
    """Draw every undrawn site in `region` whose group is known. Flushes; the
    caller commits."""
    if region in NEVER_TESTED:
        raise DrawRefused(f"{region} records never count toward a test pool (D-NJ)")
    if not 0 < share <= 0.5:
        raise DrawRefused("share must be above 0 and at most 0.5")
    if not drawn_by.strip():
        raise DrawRefused("drawn_by must name who ran the draw")

    earlier = (
        await session.execute(
            select(SiteDraw.share, SiteDraw.salt).where(SiteDraw.region == region).limit(1)
        )
    ).first()
    if earlier is not None and earlier.share != share:
        raise DrawRefused(
            f"{region} was drawn at a share of {earlier.share}; its blocks were cut for that "
            "share, so later draws must use it too"
        )
    salt = earlier.salt if earlier is not None else secrets.token_hex(16)

    report = DrawReport(region=region, share=share)
    drawn_ids = set(
        (await session.scalars(select(SiteDraw.location_id).where(SiteDraw.region == region))).all()
    )
    report.already_drawn = len(drawn_ids)
    sites = (
        await session.scalars(
            select(Location)
            .where(Location.region == region, Location.merged_into_id.is_(None))
            .order_by(Location.created_at, Location.id)
        )
    ).all()

    # Each group's sequence continues where the last draw left it.
    positions: dict[tuple[str, str], int] = defaultdict(int)
    counted = await session.execute(
        select(SiteDraw.kind_of_site, SiteDraw.setting, func.count())
        .where(SiteDraw.region == region)
        .group_by(SiteDraw.kind_of_site, SiteDraw.setting)
    )
    for kind, setting, count in counted.all():
        positions[(kind, setting)] = count

    size = block_size(share)
    now = datetime.now(timezone.utc)
    for site in sites:
        if site.id in drawn_ids:
            continue
        if site.kind_of_site is None or site.setting is None:
            report.waiting_for_group += 1
            continue
        group = (site.kind_of_site, site.setting)
        position = positions[group]
        positions[group] += 1
        is_test = position % size == _test_position(salt, region, group, position // size, size)
        session.add(
            SiteDraw(
                location_id=site.id, region=region, kind_of_site=group[0], setting=group[1],
                position=position, is_test=is_test, share=share, salt=salt,
                drawn_by=drawn_by.strip(), drawn_at=now,
            )
        )
        report.drawn += 1
        report.by_group[group] += 1
        if is_test:
            report.test += 1
            report.test_by_group[group] += 1
    await session.flush()
    return report


async def frozen_sites_document(session: AsyncSession) -> dict:
    """Every frozen test site, as plain data for `ml/`, which shares no code with
    `app/`. Training refuses these (`ml/modality/splits.py`)."""
    rows = (
        await session.execute(
            select(SiteDraw.location_id, SiteDraw.region, SiteDraw.kind_of_site, SiteDraw.setting)
            .where(SiteDraw.is_test.is_(True))
            .order_by(SiteDraw.region, SiteDraw.location_id)
        )
    ).all()
    return {
        "test_sites": [
            {"location_id": str(r.location_id), "region": r.region, "group": [r.kind_of_site, r.setting]}
            for r in rows
        ]
    }
