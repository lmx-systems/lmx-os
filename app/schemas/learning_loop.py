from pydantic import BaseModel


class ProposedRuleSummary(BaseModel):
    proposed_rule_id: str
    shop_id: str
    rule_type: str
    proposed_change: dict
    confidence: float
    supporting_annotation_count: int


class NightlyJobResult(BaseModel):
    hub_id: str
    proposals_created: list[ProposedRuleSummary]
