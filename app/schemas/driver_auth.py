from pydantic import BaseModel, Field


class SignInBody(BaseModel):
    # The code from the ops console: typed, grouped or not, or the QR payload.
    code: str = Field(min_length=1, max_length=64)
    # Stable per-install id, generated once client-side and persisted in
    # SecureStore - not an OS advertising id. Lets a specific device's
    # session be revoked later without invalidating every device this
    # driver has ever signed in on.
    device_id: str = Field(min_length=1, max_length=128)
    device_name: str | None = Field(default=None, max_length=120)


class AuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DriverDeviceView(BaseModel):
    device_id: str
    device_name: str | None = None
    last_seen_at: str
    is_current: bool


class PushTokenBody(BaseModel):
    device_id: str
    expo_push_token: str
