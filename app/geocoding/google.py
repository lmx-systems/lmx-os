"""
Google Geocoding API provider (docs/ROADMAP.md L12).

Replaces Nominatim for anything beyond a pilot. Nominatim was always labelled a
pilot decision for two reasons, and both now matter: its usage policy forbids
commercial bulk use, which running a paying client on it would violate; and its
one-request-per-second ceiling is what makes a 25-row bulk paste of new addresses
take 25 seconds. Google's limits are high enough that the Redis throttle
`NominatimGeocoder` needs is simply absent here - removing that ceiling is half
the point of this provider.

Slots in behind `BaseGeocoder` with no change to any caller: everything goes
through `app/geocoding/cache.py::resolve_address`, so the once-ever-per-address
guarantee, the negative caching and the normalization all carry over unchanged.

**The status codes are the substance of this file.** Google returns HTTP 200 for
almost everything and puts the real outcome in a `status` field, so treating a
200 as success would silently turn an exhausted quota into "that address doesn't
exist" - and because the cache remembers a no-match and never retries it, that
would permanently poison every address attempted during an outage. The mapping
below is what keeps `ZERO_RESULTS` (the address is not real) separate from
`OVER_QUERY_LIMIT` and `REQUEST_DENIED` (we could not ask).
"""
from __future__ import annotations

import httpx
import structlog

from app.geocoding.base import BaseGeocoder, GeocodeResult, GeocoderUnavailableError

logger = structlog.get_logger(__name__)

_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"

# The only status that means "this address is not real". Everything else is
# either success or our problem.
_NO_MATCH = "ZERO_RESULTS"

# Our problem, in ways worth telling apart in the logs. Each of these must raise
# rather than return None, or a transient failure gets cached as a permanent one.
_OUR_FAULT = {
    # Quota or per-second rate exceeded - retryable, and the single most likely
    # failure in production.
    "OVER_QUERY_LIMIT",
    # Key missing, invalid, restricted to the wrong API, or billing not enabled.
    # Looks like nothing works at all; it is a configuration problem.
    "REQUEST_DENIED",
    # Malformed query - ours to fix, not the address's fault.
    "INVALID_REQUEST",
    "UNKNOWN_ERROR",
}


class GoogleGeocoder(BaseGeocoder):
    provider_name = "google"

    def __init__(self, *, api_key: str, timeout_seconds: float = 5.0) -> None:
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=timeout_seconds)

    async def geocode(self, address: str) -> GeocodeResult | None:
        try:
            response = await self._http.get(
                _ENDPOINT, params={"address": address, "key": self._api_key}
            )
        except httpx.HTTPError as exc:
            raise GeocoderUnavailableError(f"geocoding request failed: {exc}") from exc

        if response.status_code != 200:
            raise GeocoderUnavailableError(f"geocoding returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise GeocoderUnavailableError("geocoding returned a non-JSON body") from exc

        status = payload.get("status")

        if status == _NO_MATCH:
            # A real answer: this address does not resolve. Safe to cache.
            logger.info("geocode_no_match", provider=self.provider_name)
            return None

        if status in _OUR_FAULT:
            # error_message carries Google's explanation, which for REQUEST_DENIED
            # is usually the actual fix ("billing not enabled", "API not
            # activated"). Worth surfacing rather than swallowing.
            detail = payload.get("error_message") or status
            logger.warning(
                "geocode_provider_error", provider=self.provider_name, status=status, detail=detail
            )
            raise GeocoderUnavailableError(f"{status}: {detail}")

        if status != "OK":
            # An unrecognised status. Treated as our problem rather than a
            # no-match, because guessing that an unknown code means "address
            # doesn't exist" is exactly the mistake this file exists to avoid.
            raise GeocoderUnavailableError(f"unexpected geocoding status {status!r}")

        results = payload.get("results") or []
        if not results:
            # OK with no results shouldn't happen, but if it does it is a
            # malformed response rather than a no-match.
            raise GeocoderUnavailableError("geocoding returned OK with no results")

        top = results[0]
        try:
            location = top["geometry"]["location"]
            lat = float(location["lat"])
            lng = float(location["lng"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocoderUnavailableError("could not read coordinates from response") from exc

        return GeocodeResult(
            lat=lat,
            lng=lng,
            display_name=str(top.get("formatted_address", "")),
            provider=self.provider_name,
        )

    async def aclose(self) -> None:
        await self._http.aclose()
