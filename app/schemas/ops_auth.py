"""Schemas for ops-dashboard auth (app/api/ops_auth_routes.py)."""
from pydantic import BaseModel


class OpsLoginBody(BaseModel):
    email: str
    password: str


class OpsAuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class OpsProfileView(BaseModel):
    ops_user_id: str
    email: str
    name: str
