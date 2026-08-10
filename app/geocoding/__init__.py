"""
Geocoding: a typed address to coordinates, cached once ever
(docs/LMX_LINK_PLAN.md §1.2).

Note the difference from every other external dependency in this codebase.
Twilio, Rippling, Stripe, Expo and S3 all follow "unconfigured -> stub", where
the stub is a usable degraded mode: an SMS that logs instead of sending still
leaves the app working. **Geocoding has no such mode.** A stub can only fail (no
order ever routes) or invent coordinates (a driver is sent to a fictional
address). So the default is a real provider, and it is one that needs no account
precisely so that "unconfigured" never happens.

`GEOCODER_PROVIDER` exists to make the eventual swap to a keyed provider a config
change rather than a code change. Today there is one implementation.
"""
from app.config import settings
from app.geocoding.base import (
    BaseGeocoder,
    GeocodeResult,
    GeocoderUnavailableError,
    normalize_address,
)
from app.geocoding.cache import resolve_address
from app.geocoding.google import GoogleGeocoder
from app.geocoding.nominatim import NominatimGeocoder

import structlog

logger = structlog.get_logger(__name__)

_geocoder: BaseGeocoder | None = None


def get_geocoder() -> BaseGeocoder:
    """The configured geocoder, created once.

    Cached at module level rather than per call because the underlying
    httpx.AsyncClient holds a connection pool - building one per address would
    discard connection reuse against a provider we are already rate-limited
    against.
    """
    global _geocoder
    if _geocoder is not None:
        return _geocoder

    provider = (settings.geocoder_provider or "nominatim").lower()

    if provider == "google":
        # Refuse rather than fall back to Nominatim. Someone who set this to
        # "google" expects Google, and quietly geocoding a paying client's
        # addresses against OSM instead would be both a licensing problem and a
        # silent return of the 1-req/sec ceiling they set this to escape.
        if not settings.google_maps_api_key:
            raise ValueError(
                "GEOCODER_PROVIDER=google but GOOGLE_MAPS_API_KEY is unset - refusing "
                "to fall back to Nominatim, whose terms forbid commercial use"
            )
        logger.info("geocoder_selected", provider="google")
        _geocoder = GoogleGeocoder(api_key=settings.google_maps_api_key)
        return _geocoder

    if provider != "nominatim":
        raise ValueError(
            f"unknown GEOCODER_PROVIDER {provider!r} - expected 'google' or 'nominatim'"
        )

    logger.warning(
        "geocoder_selected",
        provider="nominatim",
        note=(
            "PILOT provider - no account required, 1 req/sec, and its usage policy "
            "forbids commercial bulk use. Set GEOCODER_PROVIDER=google with a key "
            "before running a paying client."
        ),
    )
    _geocoder = NominatimGeocoder()
    return _geocoder


__all__ = [
    "BaseGeocoder",
    "GeocodeResult",
    "GeocoderUnavailableError",
    "get_geocoder",
    "normalize_address",
    "resolve_address",
]
