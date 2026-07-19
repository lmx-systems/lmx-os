"""
Schemas for the driver-facing API (app/api/driver_routes.py) - screens
1a-1m of LMX Driver App Wireframes.dc.html. See docs/NEXT_STEPS.md item 12
for the gap analysis this closes.
"""
from datetime import datetime

from pydantic import BaseModel


class DriverProfileView(BaseModel):
    driver_id: str
    hub_id: str
    name: str
    phone: str
    status: str
    vehicle_type: str | None = None
    plate_number: str | None = None
    delivery_zone: str | None = None

    @property
    def setup_complete(self) -> bool:
        return self.vehicle_type is not None


class DriverProfileUpdate(BaseModel):
    """Screen 1c, 'Vehicle & profile setup'."""

    vehicle_type: str  # car | van | bike
    plate_number: str
    delivery_zone: str


class DriverAvailabilityUpdate(BaseModel):
    """Screen 1d/1e's online/offline toggle."""

    status: str  # available | off_shift | on_break | en_route


class OfferStopSummary(BaseModel):
    order_id: str
    lat: float
    lng: float
    sla_tier: str
    shop_name: str = ""


class JobOfferView(BaseModel):
    offer_id: str
    hub_id: str
    expires_at: datetime
    stops: list[OfferStopSummary]

    @property
    def stop_count(self) -> int:
        return len(self.stops)


class StopView(BaseModel):
    stop_id: str
    sequence: int
    stop_type: str  # pickup | dropoff
    status: str
    lat: float
    lng: float
    shop_name: str | None = None
    address: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    notes: str | None = None
    parcel_count: int
    scanned_count: int
    order_ids: list[str]
    eta: datetime | None = None
    completed_at: datetime | None = None


class RouteView(BaseModel):
    route_id: str
    status: str
    plan_version: int
    stops: list[StopView]


class ScanParcelsBody(BaseModel):
    scanned_count: int


class CompleteStopBody(BaseModel):
    """Proof of delivery, screen 1m."""

    method: str  # photo | signature | pin
    photo_url: str | None = None
    signature_url: str | None = None
    pin: str | None = None
