from pydantic import BaseModel


class RequestOtpBody(BaseModel):
    phone: str


class RequestOtpResult(BaseModel):
    ok: bool
    # Only populated when no SMS provider is configured - see
    # app/driver_auth/otp_store.py's docstring. Never set once Twilio is
    # wired for real; a driver app build pointed at a prod-configured
    # backend simply won't receive this field.
    debug_code: str | None = None


class VerifyOtpBody(BaseModel):
    phone: str
    code: str
    # Stable per-install id, generated once client-side and persisted in
    # SecureStore - not an OS advertising id. Lets a specific device's
    # session be revoked later without invalidating every device this
    # driver has ever signed in on.
    device_id: str
    device_name: str | None = None


class AuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DriverDeviceView(BaseModel):
    device_id: str
    device_name: str | None = None
    last_seen_at: str
    is_current: bool
