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
from app.geocoding.base import BaseGeocoder, GeocodeResult, normalize_address
from app.geocoding.cache import resolve_address
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
    if provider != "nominatim":
        # Fail loudly rather than silently falling back: someone setting this to
        # "google" expects Google, and quietly geocoding against OSM instead
        # would be a wrong-addresses-in-production class of surprise.
        raise ValueError(
            f"unknown GEOCODER_PROVIDER {provider!r} - only 'nominatim' is implemented"
        )

    logger.info(
        "geocoder_selected",
        provider="nominatim",
        note="pilot provider - no account required, 1 req/sec, not for production volume",
    )
    _geocoder = NominatimGeocoder()
    return _geocoder


__all__ = [
    "BaseGeocoder",
    "GeocodeResult",
    "get_geocoder",
    "normalize_address",
    "resolve_address",
]
