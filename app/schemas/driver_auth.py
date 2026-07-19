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


class AuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
