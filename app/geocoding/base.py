"""
Geocoder interface: a typed street address in, coordinates out
(docs/LMX_LINK_PLAN.md §1.2, "Destination" and the ad-hoc pickup origin).

Why this is load-bearing rather than a nicety. LMX Link lets a client type an
address for a place we have no record of. Nothing downstream can work with text:
the batch-hold queue clusters on coordinates, the optimizer routes on
coordinates, and `app/api/driver_routes.py` renders a pickup stop from
coordinates - defaulting to 0.0, 0.0 when it has none, which puts a driver's
stop in the Gulf of Guinea. So an address that fails to resolve must be a
visible failure, never a silent fallback.

**This interface deliberately returns None rather than a best guess.** A wrong
coordinate is worse than no coordinate: no coordinate stops an order from being
dispatched, while a wrong one sends a real van to the wrong place. Callers decide
what to do with None; they never get a plausible-looking lie.

Unlike Twilio/Rippling/Stripe there is no stub fallback here, and that is
deliberate too. A stub geocoder can only fail (in which case no order ever
routes) or invent coordinates (in which case drivers go to fictional addresses).
Neither is a usable degraded mode, so the default provider is a real one -
see app/geocoding/__init__.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class GeocoderUnavailableError(Exception):
    """The provider could not answer, for reasons that are ours rather than the
    address's - exhausted quota, a rejected key, a network failure, a response we
    couldn't parse.

    **This distinction is load-bearing, not pedantry.** The address cache
    (app/geocoding/cache.py) remembers a failed lookup so a resubmitted typo
    doesn't burn the request budget three times - and it never retries what it
    remembered. If "we couldn't ask" and "this address doesn't exist" were the
    same outcome, one quota exhaustion or one expired API key would permanently
    mark every address attempted during it as unresolvable, and every future
    order to those addresses would be refused with nothing to explain why.

    So: return None for a definitive no-match, raise this for anything else.
    Callers must not cache a raise.
    """


@dataclass(frozen=True)
class GeocodeResult:
    """One resolved address."""

    lat: float
    lng: float
    # What the provider thinks this address is, canonically. Kept because it is
    # the thing worth showing a user for confirmation - "did you mean this?" -
    # rather than echoing back what they typed.
    display_name: str
    provider: str


class BaseGeocoder(ABC):
    """Turn an address into coordinates, or admit it can't."""

    provider_name: str

    @abstractmethod
    async def geocode(self, address: str) -> GeocodeResult | None:
        """Resolve one address.

        Returns None ONLY when the provider definitively says this address does
        not resolve - a typo, a made-up street. That outcome is cached, so it
        must mean "asked and answered no", never "couldn't ask".

        Raises `GeocoderUnavailableError` for everything else: exhausted quota, a
        rejected key, a timeout, an unparseable response. See that exception for
        why conflating the two would poison the cache.
        """
        raise NotImplementedError


def normalize_address(address: str) -> str:
    """The cache key for an address.

    Deliberately conservative: case-fold, collapse whitespace, and drop
    trailing punctuation. It does NOT try to canonicalize "St" vs "Street" or
    reorder components, because two addresses that differ only in abbreviation
    are usually the same place - but two that differ in a house number are not,
    and an over-eager normalizer that merged them would send a driver next door.

    Under-normalizing costs a duplicate geocode and a duplicate cache row.
    Over-normalizing costs a wrong delivery. The asymmetry decides the design.
    """
    collapsed = " ".join(address.split())
    return collapsed.strip(" ,.;").casefold()
