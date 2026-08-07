"""
Public client signup and the LMX approval that gates it
(docs/LMX_LINK_PLAN.md).

This reverses roadmap item `C5`, which recorded self-serve signup as deliberately
absent - "a B2B onboarding relationship, not self-serve SaaS". The approval gate
is what preserves that posture rather than abandoning it: signup is open to
anyone, but nobody dispatches an LMX van until a human at LMX says so.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SignupStatus = Literal["pending", "active", "rejected"]


def _looks_like_an_email(value: str) -> str:
    """A deliberately shallow check.

    Plain `str` rather than pydantic's `EmailStr` to match the rest of this
    codebase - `ClientUser.email` and `POST /admin/clients`'s `portal_email` are
    both plain strings - and because `EmailStr` needs the `email-validator`
    package, which is not a dependency here. Adding one for a single field on one
    form is not worth it.

    This catches the mistakes that actually happen on a form (a missing @, a
    stray space, a truncated domain). It is not RFC 5322 and does not try to be;
    the real proof an address works is that mail sent to it arrives, which is a
    later problem than validation.
    """
    cleaned = value.strip()
    if " " in cleaned or "@" not in cleaned:
        raise ValueError("not a valid email address")
    local, _, domain = cleaned.rpartition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("not a valid email address")
    return cleaned.lower()


class ClientSignupBody(BaseModel):
    """What a prospective client fills in. Deliberately short.

    Every field here is something an applicant knows off the top of their head.
    Nothing that needs a contract, a rate negotiation or their IT department -
    those come later, and requiring them now is where a funnel dies.
    """

    company_name: str = Field(min_length=1, max_length=160)
    contact_name: str = Field(min_length=1, max_length=120)
    contact_email: str = Field(min_length=3, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=32)
    # Free text - "Austin metro", "78702 and nearby". Hubs have no service-area
    # model, so ops reads this at approval to place the client on a hub.
    service_area: str = Field(min_length=1, max_length=255)

    # The password for their first portal login, set now so approval doesn't have
    # to mint and transmit a credential. The account stays inactive until
    # approved, so a password existing early grants nothing.
    password: str = Field(min_length=10, max_length=128)

    # Not a checkbox but a record: which version they agreed to. What the terms
    # actually say has to exist before this form goes live - that is a legal
    # artifact, not an engineering one, and it ties to R3 and W7.
    terms_version: str = Field(min_length=1, max_length=32)
    accepted_terms: bool

    @field_validator("contact_email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        return _looks_like_an_email(value)

    @model_validator(mode="after")
    def _terms_must_actually_be_accepted(self) -> "ClientSignupBody":
        """Refuse rather than record `accepted_terms=False`.

        A stored client whose terms were never accepted is a liability sitting in
        the database - it looks like a real applicant to ops, and the fact that
        nothing was agreed to is one boolean away from being missed at approval.
        """
        if not self.accepted_terms:
            raise ValueError("terms must be accepted to sign up")
        return self


class ClientSignupResult(BaseModel):
    """Deliberately says almost nothing.

    No client id, no next step beyond "we'll be in touch". An unauthenticated
    caller has no business learning our internal identifiers, and a signup that
    promised ordering access before a human approved it would be lying.
    """

    status: SignupStatus
    message: str


class PendingSignupView(BaseModel):
    """One applicant, for the ops review queue."""

    client_id: str
    company_name: str
    service_area: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    terms_version: str | None
    terms_accepted_at: datetime | None
    signup_status: SignupStatus
    submitted_at: datetime
    # Provisional at signup; ops confirms or changes it on approval.
    hub_id: str


class ApproveRateInput(BaseModel):
    sla_tier: str
    rate_per_drop_cents: int = Field(ge=0)


class ApproveSignupBody(BaseModel):
    """Approval is also where rates get set, and that is the point.

    It means an active client always has rates, so `Order.fee_cents` is never
    null for them - which removes the whole "default rate card" problem that a
    signup-without-rates would otherwise create. A client we approved but cannot
    bill is worse than one still waiting.
    """

    # Required, not optional: approving without rates is the failure mode this
    # endpoint exists to prevent.
    rates: list[ApproveRateInput] = Field(min_length=1)
    # Confirms or corrects the provisional hub chosen at signup.
    hub_id: str | None = None


class RejectSignupBody(BaseModel):
    # Recorded rather than shown to the applicant. Useful when the same company
    # applies again, and the alternative is ops re-deciding from nothing.
    reason: str | None = Field(default=None, max_length=500)


class SignupDecisionResult(BaseModel):
    client_id: str
    signup_status: SignupStatus
    # Null on rejection - nothing was created to charge against.
    rates_created: int | None = None
