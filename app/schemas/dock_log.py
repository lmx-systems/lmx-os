"""The public Dock Log's request and response bodies (`DRV-7`).

The driver app's `DockSurveyBody` is the sibling of this, and the two are
deliberately the same eight fields with the same names. `ROADMAP_1.5.md`'s
`DRV-7` row requires it: *"its answer set moves to these vocabularies rather
than being translated afterwards."* Two shapes would mean a translation, and a
translation is a second place the vocabulary lives.

What this adds is the part a stranger has to supply and a driver never does:
**which dock this is.** Our driver is standing at a stop we dispatched, so the
server already knows. A member of the public has no stop, no account and no
`Location`, so they give coordinates and a name, and a person matches it later.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DockLogSubmissionBody(BaseModel):
    """One dock, surveyed by somebody who does not work for us.

    **Every answer is optional and the identity fields are not.** Same
    reasoning as `DockSurveyBody` — a partial survey is worth more than a
    guessed one, and making it harder to skip a question than to answer it is
    how you get careless answers. But a survey nobody can attach to a place is
    worth nothing at all, so `business_name` is required: coordinates alone
    describe a point on a map, and the person doing the matching needs
    something to recognise.

    The endpoint additionally refuses a body where every answer is null. The
    driver app's equivalent accepts one and returns 200, because there the
    submission itself carries information — that driver saw this dock and had
    nothing to say. Here it carries none and costs a reviewer a row.
    """

    # Required. Not because we trust it, but because somebody has to read it.
    business_name: str = Field(min_length=1, max_length=200)
    submitted_address: str | None = Field(default=None, max_length=300)

    # From the browser, if consent was given. A survey with an address and no
    # coordinates is still matchable by a person; one with neither is not, and
    # the endpoint says so.
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)

    # The eight. Validated against `app/models/receiver_profile.py`'s tuples at
    # the service layer, not here, so `hand_carry` means one thing in one place.
    stop_point: str | None = None
    curb_access: str | None = None
    walk_distance_band: str | None = None
    door_path: str | None = None
    obstruction: str | None = None
    who_receives: str | None = None
    landing_surface: str | None = None
    appointment_required: bool | None = None


class DockLogSubmissionResult(BaseModel):
    """Almost nothing, deliberately.

    The same posture `client_signup` takes and for the same reason: an
    unauthenticated caller has no business learning our identifiers. No
    submission id, no matched `Location`, no indication of whether this dock is
    one we already hold — that last one especially, because a response that
    differed would turn this form into a way to enumerate our customers' docks
    one address at a time.

    `answers_recorded` is the one fact worth returning. A submitter who skipped
    more than they meant to should be able to see that, and it reveals nothing
    they did not just type.
    """

    accepted: bool
    answers_recorded: int


class DockLogCandidateView(BaseModel):
    """A dock near the submitted coordinates. A shortlist entry, not a match."""

    location_id: str
    address: str


class DockLogSubmissionView(BaseModel):
    """One row in the review queue.

    Carries the answers as a dict rather than eight nullable fields: the screen
    renders whatever was answered, and a fixed shape would put seven empty rows
    on a submission that answered one question. `submitted_from_ip` is
    deliberately absent — it exists to make a flood attributable afterwards,
    not to be shown to whoever happens to open the queue.
    """

    submission_id: str
    business_name: str | None
    submitted_address: str | None
    lat: float | None
    lng: float | None
    created_at: str
    answers_recorded: int
    answers: dict[str, object]
    candidates: list[DockLogCandidateView]


class DockLogImportBody(BaseModel):
    """Which dock this submission is, decided by a person.

    The only field, and that is the design. Everything else the import needs is
    already on the row; what it cannot have is an opinion about identity,
    because the submitter chose every value that could supply one.
    """

    location_id: str


class DockLogRejectBody(BaseModel):
    """Why this will never be imported.

    Required, the same way `CON-2`'s override reason is: a dismissal with no
    reason is indistinguishable from one nobody thought about, and this queue
    is the only place the public surface's abuse becomes visible.
    """

    reason: str = Field(min_length=1, max_length=200)


class DockLogReviewResult(BaseModel):
    submission_id: str
    imported: bool
    rejected: bool
