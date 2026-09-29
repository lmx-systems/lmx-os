"""A dock survey from somebody who does not work for us (`DRV-7`).

`ROADMAP_1.5.md`'s `DRV-7` row states the rule this table exists to enforce:
**a stranger's submission must never write to `receiver_profiles` directly.**

That is not squeamishness about data quality. `receiver_profiles` is the layer
`M5` trains on, so an unauthenticated form writing into it lets anybody on the
internet label our training data — and the damage would never appear as an
error. It would appear months later as a model confident that a dock has no
legal stopping place because one person said so, with nothing in the record to
say where the claim came from.

So a submission is inert. It names no dock until somebody matches it
(`location_id`), changes nothing until somebody imports it (`imported_at`), and
a rejected one is kept rather than deleted — a pattern of junk from one address
is evidence, and a deleted row is not.

## Why the answers are the same vocabulary, not a parallel one

The roadmap row is explicit: *"its answer set moves to these vocabularies
rather than being translated afterwards."* A translation layer between a public
form and `receiver_profiles` would be a second place the vocabulary lives, and
the two would drift the first time a value was added to one of them. Every
column here is the same name, the same width and validated against the same
tuple as its counterpart in `app/models/receiver_profile.py`.

## Coordinates are the only identity a stranger can give

They have no `Location`, no account, and no reason to know how we normalise an
address. `lat`/`lng` come from the browser's geolocation, so they are nullable
(consent can be refused, and a survey with an address alone is still worth
having) and frequently imprecise. `business_name` and `submitted_address` are
free text for the person doing the matching — never parsed, never matched on.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class DockLogSubmission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dock_log_submissions"

    # What the submitter says this place is. For a human's eyes only.
    business_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    submitted_address: Mapped[str | None] = mapped_column(String(300), nullable=True)

    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    # The eight answers. Same names, widths and vocabularies as
    # `ReceiverProfile`, so an import is a copy rather than a translation.
    stop_point: Mapped[str | None] = mapped_column(String(16), nullable=True)
    curb_access: Mapped[str | None] = mapped_column(String(16), nullable=True)
    walk_distance_band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    door_path: Mapped[str | None] = mapped_column(String(16), nullable=True)
    obstruction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    who_receives: Mapped[str | None] = mapped_column(String(16), nullable=True)
    landing_surface: Mapped[str | None] = mapped_column(String(16), nullable=True)
    appointment_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Null until somebody decides which dock this is. Nothing infers it.
    location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id"), nullable=True, index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_ops_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ops_users.id"), nullable=True
    )
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Not displayed and not joined to a person. It is the only thing that makes
    # a flood attributable afterwards, and the rate limiter keys on it too.
    submitted_from_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    @property
    def answered_count(self) -> int:
        """How many of the eight questions carry an answer.

        A submission with one answer is not worth a reviewer's attention and a
        submission with none is not worth storing; the endpoint refuses the
        latter. Counted here rather than stored so it cannot disagree with the
        columns it describes.
        """
        return sum(
            value is not None
            for value in (
                self.stop_point,
                self.curb_access,
                self.walk_distance_band,
                self.door_path,
                self.obstruction,
                self.who_receives,
                self.landing_surface,
                self.appointment_required,
            )
        )
