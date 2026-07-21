"""
Read-model for the hubs table (roadmap item D1) - built for hub pickers
in the internal dashboard (fleet view, onboarding form). Deliberately
thin: no counts/rollups, just what a dropdown needs.
"""
from __future__ import annotations

from pydantic import BaseModel


class HubView(BaseModel):
    hub_id: str
    name: str
    timezone: str
    lat: float
    lng: float
    active: bool
