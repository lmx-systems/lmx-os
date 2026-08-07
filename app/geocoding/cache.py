"""
Cache-first address resolution (docs/LMX_LINK_PLAN.md §1.2).

**Everything in the application calls `resolve_address` and nothing calls a
geocoder directly.** That is the point of this module: it is where the
once-ever-per-address guarantee lives, and it is what makes a rate-limited
no-account provider workable. Bypassing it would quietly turn
new-addresses-per-day back into orders-per-day.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.geocoding.base import BaseGeocoder, GeocodeResult, normalize_address
from app.models.geocoded_address import GeocodedAddress

logger = structlog.get_logger(__name__)


async def _lookup(session: AsyncSession, normalized: str) -> GeocodedAddress | None:
    result = await session.execute(
        select(GeocodedAddress).where(GeocodedAddress.normalized_address == normalized)
    )
    return result.scalar_one_or_none()


async def resolve_address(
    session: AsyncSession, address: str, *, geocoder: BaseGeocoder
) -> GeocodeResult | None:
    """Coordinates for an address, from cache when we have them.

    Returns None when the address cannot be resolved - including when we tried
    before and failed. Callers must treat None as "not dispatchable yet" rather
    than substituting a default: `app/api/driver_routes.py` renders a pickup stop
    from coordinates and falls back to 0.0, 0.0, which puts a driver's stop in
    the Gulf of Guinea.

    Commits the cache row itself. That is a deliberate scope choice: a cached
    geocode is independently useful even if the caller's own transaction later
    rolls back, and re-asking a rate-limited provider for something we already
    know is worse than an orphaned cache row.
    """
    normalized = normalize_address(address)
    if not normalized:
        return None

    cached = await _lookup(session, normalized)
    if cached is not None:
        if not cached.resolved:
            # A remembered failure. Deliberately not retried here - see the
            # model docstring on why a typo'd address that gets resubmitted
            # shouldn't burn the request budget three times.
            logger.info("geocode_cache_hit_unresolved", normalized=normalized)
            return None
        return GeocodeResult(
            lat=cached.lat,
            lng=cached.lng,
            display_name=cached.display_name or "",
            provider=cached.provider or "cache",
        )

    result = await geocoder.geocode(address)

    row = GeocodedAddress(
        normalized_address=normalized,
        raw_address=address[:255],
        lat=result.lat if result else None,
        lng=result.lng if result else None,
        display_name=result.display_name[:500] if result else None,
        provider=result.provider if result else geocoder.provider_name,
        attempted_at=datetime.now(timezone.utc),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # Two requests geocoded the same new address concurrently. Harmless -
        # the other writer's row is as good as ours, so take theirs rather than
        # failing a customer's order submission over a cache race.
        await session.rollback()
        existing = await _lookup(session, normalized)
        if existing is not None and existing.resolved:
            return GeocodeResult(
                lat=existing.lat,
                lng=existing.lng,
                display_name=existing.display_name or "",
                provider=existing.provider or "cache",
            )
        return result

    return result
