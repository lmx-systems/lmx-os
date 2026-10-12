"""Shapes for the labelling queue (`DE-9`, app/record/labelling.py)."""
from datetime import datetime

from pydantic import BaseModel, Field


class LabellingRecordView(BaseModel):
    """One record, with what is known about it - and never anybody's verdict."""

    stop_id: str
    tier: str
    elements_missing: int
    record_source: str | None
    recorded_at: datetime | None
    address: str | None
    region: str | None
    kind_of_site: str | None
    setting: str | None
    # The handoff point, as captured. Null is unknown.
    handoff_type: str | None
    stop_type: str | None
    stop_legal: bool | None
    walk_distance_m: float | None
    steps_or_ramps: str | None
    continuous_sidewalk: bool | None
    obstructions: list | None
    drone_open_ground: bool | None
    overhead: list | None
    photo_approach: str | None
    photo_stop_point: str | None
    photo_path: str | None
    photo_handoff: str | None
    # The item.
    item_description: str | None
    item_weight_kg: float | None
    item_size_class: str | None
    item_hazmat_or_liquid: bool | None
    # Who receives, and when.
    receiving_hours: dict | None
    who_receives: str | None


class LabellingQueuePage(BaseModel):
    """The scorer's next records, the limits they are scored against, and the
    words a reason can be given in."""

    machine_limits_version: str | None
    machines: list[str]
    no_reasons: list[str]
    missing_data: list[str]
    records: list[LabellingRecordView]


class MachineVerdictInput(BaseModel):
    verdict: str
    reason_codes: list[str] = Field(default_factory=list)
    confidence: float | None = None


class LabelVerdictsBody(BaseModel):
    """All three machines at once (app/record/labelling.py says why)."""

    machine_limits_version: str = Field(min_length=1, max_length=32)
    verdicts: dict[str, MachineVerdictInput]


class LabelSavedView(BaseModel):
    stop_id: str
    saved: int


class AgreementCellView(BaseModel):
    machine: str
    group: str
    both_scored: int
    agreement: float | None
    under_gate: bool | None


class AgreementView(BaseModel):
    gate: float
    cells: list[AgreementCellView]
