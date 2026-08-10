"""
Nominatim geocoder - the pilot provider (docs/LMX_LINK_PLAN.md).

Chosen because it needs no account and no API key, which matters: the Google
Cloud project that would back a keyed provider is the same one still blocking
`E1` in docs/ROADMAP.md, and waiting on procurement would have blocked the whole
ad-hoc pickup path.

**THIS IS A PILOT DECISION, NOT A PRODUCTION ONE.** Nominatim is run by OSM on
donated infrastructure and its usage policy is explicit: an absolute maximum of
one request per second, a genuine User-Agent identifying the application, and no
heavy or commercial bulk use. Both requirements are implemented below. Real
volume - or the commercial terms that come with it - will force a keyed provider,
which is why `BaseGeocoder` exists and why every caller goes through
`app/geocoding/cache.py` rather than here directly. Swapping providers should
touch this file and the factory, nothing else.

The address cache is what makes this viable at all: an address is geocoded once
ever, so steady-state request volume is new-addresses-per-day, not
orders-per-day. §2.2 principle 3 ("most distributors deliver to the same 40-80
shops forever") is the reason that ratio is so favourable.
"""
from __future__ import annotations

import asyncio

import httpx
import structlog

from app.geocoding.base import BaseGeocoder, GeocodeResult, GeocoderUnavailableError
from app.redis_client import get_client as get_redis_client

logger = structlog.get_logger(__name__)

_ENDPOINT = "https://nominatim.openstreetmap.org/search"

# Nominatim's policy requires a User-Agent that identifies the application and
# provides a contact route. A generic client string is grounds for being blocked.
_USER_AGENT = "LMX-OS/1.0 (dispatch platform; ops@lmxit.com)"

# Their hard limit is 1 req/sec. Enforced through Redis rather than a local
# semaphore so the limit holds across app instances - two ECS tasks each politely
# self-limiting to 1/sec is 2/sec from Nominatim's side, which is a violation.
_THROTTLE_KEY = "geocode:nominatim:throttle"
_THROTTLE_TTL_SECONDS = 1

# How long a caller will wait for a throttle slot before giving up. Geocoding
# happens inside a user-facing order submission, so this is deliberately short:
# an order that can't be geocoded right now is still accepted and stored (never
# block intake on a missing field, §2.2 principle 7), it just isn't dispatchable
# until something resolves it. Blocking a counter person for ten seconds to
# avoid that is the wrong trade.
_MAX_WAIT_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 0.2


class NominatimGeocoder(BaseGeocoder):
    provider_name = "nominatim"

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self._http = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"User-Agent": _USER_AGENT},
        )

    async def _acquire_slot(self) -> bool:
        """Wait for this instance's turn under the 1 req/sec ceiling."""
        redis = get_redis_client()
        waited = 0.0
        while waited <= _MAX_WAIT_SECONDS:
            if await redis.set(_THROTTLE_KEY, "1", nx=True, ex=_THROTTLE_TTL_SECONDS):
                return True
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            waited += _POLL_INTERVAL_SECONDS
        return False

    async def geocode(self, address: str) -> GeocodeResult | None:
        if not await self._acquire_slot():
            # Raises rather than returning None: we never asked, so this must not
            # be cached as "this address does not resolve". Sustained contention
            # here is the signal that volume has outgrown Nominatim - which is
            # what GoogleGeocoder (L12) exists for.
            logger.warning(
                "geocode_throttled",
                provider=self.provider_name,
                reason="could not acquire a rate-limit slot within the wait budget",
            )
            raise GeocoderUnavailableError("rate-limit slot unavailable")

        try:
            response = await self._http.get(
                _ENDPOINT,
                params={"q": address, "format": "jsonv2", "limit": 1, "addressdetails": 0},
            )
        except httpx.HTTPError as exc:
            logger.warning("geocode_request_failed", provider=self.provider_name, error=str(exc))
            raise GeocoderUnavailableError(f"geocoding request failed: {exc}") from exc

        if response.status_code != 200:
            logger.warning(
                "geocode_bad_status", provider=self.provider_name, status=response.status_code
            )
            raise GeocoderUnavailableError(f"geocoding returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("geocode_bad_payload", provider=self.provider_name)
            raise GeocoderUnavailableError("geocoding returned a non-JSON body") from exc

        if not payload:
            # The one case that is genuinely a no-match and therefore cacheable:
            # Nominatim answered, with nothing. A typo'd address is a user event,
            # not a system fault, so info rather than warning.
            logger.info("geocode_no_match", provider=self.provider_name)
            return None

        top = payload[0]
        try:
            lat = float(top["lat"])
            lng = float(top["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("geocode_unparseable_coords", provider=self.provider_name)
            raise GeocoderUnavailableError("could not read coordinates from response") from exc

        return GeocodeResult(
            lat=lat,
            lng=lng,
            display_name=str(top.get("display_name", "")),
            provider=self.provider_name,
        )

    async def aclose(self) -> None:
        await self._http.aclose()
