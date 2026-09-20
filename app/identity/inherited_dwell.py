"""Dwell measured by a previous operator at the same dock (`IDN-4`).

`M1`'s problem is cold start. A dock we have never delivered to has no dwell of
its own, `refresh_dwell_statistics` can only compute from stops we made, and for
a new dock there are none. The design partner's second-precision export carries
real dwell for hundreds of the same physical docks, measured by whoever was
driving before us.

## Only the second-precision file

The whole-book Customer Timing export is minute-resolution, so **65.5% of its
stops compute to zero dwell** - the finding that justifies `DRV-1` in the first
place. Importing it would fill the table with zeros that look like measurements.
Only the Detail export's `dwell_sec` is real, and `ml/real/export.py::usable_dwell`
is what separates the two.

## Never merged with our own

Written to `inherited_dwell_*`, never to `dwell_p50_seconds`. Three reasons, any
one sufficient: the nightly refresh would erase it by 2am; they are different
measurements by different drivers; and `MODEL_AND_DATA_BRIEF.md` §418 - *"whether
the learning transfers is measured, not assumed"* - applies in spirit even though
this is narrower than `PRD-8`'s cross-customer question.

## Which figure answers

`dwell_estimate()` decides, and it says which one it used and why rather than
returning a bare number. There is no blending and no weighted average: a caller
that cannot tell whose observation it is holding cannot report honestly, and a
shrinkage estimator here would bury the provenance in arithmetic.

The threshold is `MIN_OWN_SAMPLES`, and it is a stated choice rather than a
derived one - see its comment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.receiver_profile import SOURCE_INHERITED, SOURCE_OBSERVED, ReceiverProfile
from app.models.shop import Shop

# Below this many of our own stops at a dock, an inherited figure is the better
# answer if there is one.
#
# **Stated, not derived.** `MODEL_AND_DATA_BRIEF.md` puts M1's per-dock target at
# ~30 observations and notes the repo's own baseline uses 10; §13 lists "is ~30
# right?" as an open question with nothing deriving it. Ten is the number this
# codebase already uses for a related decision (`_MIN_SAMPLES_FOR_P90`), so it is
# used here for consistency rather than because it is right. When the Thursday
# agenda answers that question, this moves with it.
MIN_OWN_SAMPLES = 10


@dataclass(frozen=True)
class DwellEstimate:
    """A dwell figure and whose observation it is.

    `source` is not decoration. A p50 of 240 seconds means something different
    depending on whether we measured it or inherited it, and a caller holding a
    bare integer cannot tell the difference or report it.
    """

    p50_seconds: int | None
    sample_count: int
    source: str | None
    why: str

    @property
    def is_ours(self) -> bool:
        return self.source == SOURCE_OBSERVED


def dwell_estimate(profile: ReceiverProfile | None) -> DwellEstimate:
    """The best dwell figure for one dock, and where it came from.

    Prefers our own observation once there is enough of it, falls back to an
    inherited figure, and refuses when there is neither. No blending: a weighted
    average of our two deliveries and somebody else's four hundred would be a
    number with no owner, and the provenance is the part a reader needs most.
    """
    if profile is None:
        return DwellEstimate(None, 0, None, "no profile for this dock")

    own = profile.dwell_sample_count or 0
    if own >= MIN_OWN_SAMPLES and profile.dwell_p50_seconds is not None:
        return DwellEstimate(
            profile.dwell_p50_seconds,
            own,
            SOURCE_OBSERVED,
            f"our own, {own} stops",
        )

    if profile.inherited_dwell_p50_seconds is not None:
        inherited = profile.inherited_dwell_sample_count or 0
        reason = (
            f"inherited from {profile.inherited_dwell_source}, {inherited} stops"
        )
        if own:
            # Said out loud rather than silently discarded. Somebody reading a
            # dock with both will want to know we had our own and chose not to
            # use it yet.
            reason += f" - we have {own} of our own, below the {MIN_OWN_SAMPLES} needed"
        return DwellEstimate(
            profile.inherited_dwell_p50_seconds, inherited, SOURCE_INHERITED, reason
        )

    if own and profile.dwell_p50_seconds is not None:
        return DwellEstimate(
            profile.dwell_p50_seconds,
            own,
            SOURCE_OBSERVED,
            f"our own, {own} stops - thin, and nothing inherited to fall back on",
        )

    return DwellEstimate(None, 0, None, "no dwell observed here and none inherited")


@dataclass
class ImportReport:
    """What an import matched, and - more usefully - what it did not."""

    docks_updated: int = 0
    receivers_unmatched: int = 0
    receivers_without_usable_dwell: int = 0

    def summary(self) -> str:
        return (
            f"{self.docks_updated} dock(s) given an inherited dwell; "
            f"{self.receivers_unmatched} receiver id(s) matched no shop; "
            f"{self.receivers_without_usable_dwell} had no usable dwell."
        )


async def import_inherited_dwell(
    session: AsyncSession,
    observations: dict[str, list[float]],
    *,
    source: str,
    observed_from: datetime | None = None,
    observed_to: datetime | None = None,
) -> ImportReport:
    """Attach a previous operator's dwell to the docks it belongs to.

    `observations` maps the export's receiver id to that receiver's usable dwell
    seconds. The caller does the reading and the filtering - this module does not
    know what a CSV is, and `ml/real/export.py::usable_dwell` already decides
    which rows carry a real measurement.

    **The join is `Shop.external_ref`.** `scripts/load_identity_from_export.py`
    writes the export's account id there and links the shop to a dock, so a
    receiver id reaches a `Location` through the shop rather than through
    anything invented here. A receiver id matching no shop is reported rather
    than guessed at - it means identity was never seeded for that account, which
    is a fact about the import order and not about the dock.

    Replaces rather than accumulates. Re-running an import with a corrected file
    should leave the corrected figure, not the average of two attempts.
    """
    report = ImportReport()
    if not observations:
        return report

    rows = (
        await session.execute(
            select(Shop.external_ref, Shop.location_id).where(
                Shop.external_ref.in_(observations), Shop.location_id.is_not(None)
            )
        )
    ).all()
    dock_by_ref = {ref: location_id for ref, location_id in rows}

    # Several receiver ids can reach one dock - that is what IDN-2's merging is
    # for - so the samples are pooled per dock rather than the last one winning.
    pooled: dict = {}
    for ref, dwells in observations.items():
        usable = [d for d in dwells if d is not None and d > 0]
        if not usable:
            report.receivers_without_usable_dwell += 1
            continue
        dock_id = dock_by_ref.get(ref)
        if dock_id is None:
            report.receivers_unmatched += 1
            continue
        pooled.setdefault(dock_id, []).extend(usable)

    for dock_id, samples in pooled.items():
        profile = await session.scalar(
            select(ReceiverProfile).where(ReceiverProfile.location_id == dock_id)
        )
        if profile is None:
            profile = ReceiverProfile(location_id=dock_id)
            session.add(profile)
        samples.sort()
        middle = len(samples) // 2
        median = (
            samples[middle]
            if len(samples) % 2
            else (samples[middle - 1] + samples[middle]) / 2
        )
        profile.inherited_dwell_p50_seconds = int(round(median))
        profile.inherited_dwell_sample_count = len(samples)
        profile.inherited_dwell_source = source[:64]
        profile.inherited_dwell_observed_from = observed_from
        profile.inherited_dwell_observed_to = observed_to
        report.docks_updated += 1

    await session.flush()
    return report
