"""
What counts as proof of delivery for a given order (docs/ROADMAP.md LMX Link,
LMX_LINK_PLAN.md §1.2 "Proof").

`ProofRequirements` has been on the LMX Order Object since L1 and written to
`orders.proof_requirements` at ingestion since L3 - **and read by nothing.** So the
schema advertised configurable proof while the driver app enforced a constant, which
is worse than not having the field: an aggregator order stating four photos with
named subjects completed on one photo, and a client requiring a signature got a
photo, with the object recording that we knew better.

**And the enforcement it was missing was not "the wrong kind of proof" but "no proof
at all".** `complete_stop` recorded `pod_method` and trusted it: `method="photo"` with
`photo_url` left null completed the stop. Proof of delivery proved nothing.

Two decisions worth naming:

**A commingled stop takes the UNION of its orders' requirements, i.e. the strictest.**
One dropoff can cover several orders (Section 8 clustering), and they can come from
sources with different proof rules. Taking the laxest - or the first - would mean a
client's signature requirement silently disappearing because someone else's order
happened to share the van. Being over-strict costs a driver one extra photo; being
under-strict costs a client the evidence they contracted for.

**A PIN satisfies a signature requirement.** Both answer "the right person received
this", and the PIN is the stronger of the two - it was texted to the customer and is
verified against what we issued (A4), whereas a signature is an image nobody checks.
Requiring both would be theatre.
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.stop import StopOrder
from app.schemas.lmx_order import ProofRequirements

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResolvedProof:
    """What this stop must produce."""

    photo_count_required: int
    photo_subjects: list[str]
    signature_required: bool

    @property
    def is_default(self) -> bool:
        """Whether this is the one-photo baseline the app has always assumed."""
        return (
            self.photo_count_required == 1
            and not self.photo_subjects
            and not self.signature_required
        )


class ProofNotSatisfied(Exception):
    """Carries a message written for the driver holding the phone at the door, since
    that is who has to act on it - it says what to capture, not which field failed
    validation."""


async def resolve_stop_proof(session: AsyncSession, stop_id) -> ResolvedProof:
    """The strictest requirement across every order on this stop.

    Falls back to the `ProofRequirements` defaults for an order whose
    `proof_requirements` is empty or missing - every row ingested before the contract
    landed - so an order that says nothing behaves exactly as the app always did.
    """
    result = await session.execute(
        select(Order.proof_requirements)
        .join(StopOrder, StopOrder.order_id == Order.id)
        .where(StopOrder.stop_id == stop_id)
    )
    rows = [row[0] for row in result.all()]

    photo_count = 0
    subjects: list[str] = []
    signature = False
    for raw in rows:
        try:
            requirement = ProofRequirements(**(raw or {}))
        except Exception:  # noqa: BLE001
            # A malformed blob must not make a stop uncompletable - a driver cannot
            # fix our data from the doorstep. Fall back to the default and say so.
            logger.warning("proof_requirements_unreadable", stop_id=str(stop_id))
            requirement = ProofRequirements()
        photo_count = max(photo_count, requirement.photo_count_required)
        signature = signature or requirement.signature_required
        for subject in requirement.photo_subjects:
            if subject not in subjects:
                subjects.append(subject)

    if not rows:
        # A stop with no orders attached shouldn't happen, but demanding proof for
        # nothing would strand the driver. The default is the safe answer.
        default = ProofRequirements()
        return ResolvedProof(
            photo_count_required=default.photo_count_required,
            photo_subjects=[],
            signature_required=default.signature_required,
        )

    return ResolvedProof(
        photo_count_required=photo_count,
        photo_subjects=subjects,
        signature_required=signature,
    )


def assert_proof_satisfied(
    required: ResolvedProof,
    *,
    method: str,
    photo_urls: list[str],
    signature_url: str | None,
    pin_verified: bool,
) -> None:
    """Raise `ProofNotSatisfied` unless what the driver captured meets `required`.

    **`photo_count_required` is not an absolute floor, and reading it as one is a
    mistake worth spelling out.** The app's model has always been "pick one of photo,
    signature or PIN", and `ProofRequirements`' defaults are documented as matching
    that - so the default `photo_count_required=1` means *one photo if a photo is the
    proof*, not *a photo on every delivery*. Enforcing it as a floor would have made
    every PIN delivery also take a photo: a silent operational change nobody asked
    for, and it breaks A4's PIN flow outright.

    So the rule is in three parts:

      1. **The chosen method must actually carry evidence.** This is the hole that
         mattered - `method="photo"` with a null URL used to complete the stop.
      2. **More than one photo is mandatory regardless of method.** "Four photos of
         named subjects" is a real requirement that a signature cannot stand in for;
         a client asking for it is asking for the pictures.
      3. **A signature requirement is additional**, satisfied by a signature or a
         verified PIN - both answer "the right person received this", and the PIN is
         the stronger since it is checked against what we issued.

    `pin_verified` rather than "a pin was typed": a wrong PIN is not proof, and the
    caller has already checked it against the issued value.
    """
    supplied = [url for url in photo_urls if url]

    # (1) The method has to mean something.
    if method == "photo" and not supplied:
        raise ProofNotSatisfied("Take a photo of the delivery before completing it")
    if method == "signature" and not signature_url:
        raise ProofNotSatisfied("Capture a signature before completing it")
    if method == "pin" and not pin_verified:
        raise ProofNotSatisfied("Enter the customer's PIN before completing it")

    # (2) An elevated photo count applies whatever the method is.
    if required.photo_count_required > 1 and len(supplied) < required.photo_count_required:
        missing = required.photo_count_required - len(supplied)
        detail = f"{missing} more photo{'s' if missing > 1 else ''} needed for this delivery"
        if required.photo_subjects:
            # The subjects are the whole reason a count above one exists - "four
            # photos" without saying of what produces four pictures of a doorstep.
            detail += f" - this client asks for: {', '.join(required.photo_subjects)}"
        raise ProofNotSatisfied(detail)

    # (3) A signature, or the stronger thing that stands in for it.
    if required.signature_required and not signature_url and not pin_verified:
        raise ProofNotSatisfied(
            "This delivery needs a signature from the person receiving it "
            "(a verified PIN also counts)"
        )
