"""Schemas for hub-listing (app/api/routes.py's GET /hubs)."""
from pydantic import BaseModel


class HubSummary(BaseModel):
    hub_id: str
    name: str
