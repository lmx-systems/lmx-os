"""Request/response models for ops dashboard auth (roadmap item S1)."""
from __future__ import annotations

from pydantic import BaseModel, Field

# Plain `str` for emails, matching app/schemas/admin.py's portal_email -
# EmailStr needs the email-validator extra, which this project doesn't
# pull in for one field's worth of validation.


class OpsLoginBody(BaseModel):
    email: str
    password: str


class OpsAuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class OpsUserView(BaseModel):
    user_id: str
    email: str
    name: str
    role: str
    active: bool


class OpsUserCreateBody(BaseModel):
    email: str = Field(min_length=3, max_length=255, pattern=r".+@.+\..+")
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)
    role: str = "operator"
