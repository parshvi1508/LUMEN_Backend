from pydantic import BaseModel, Field

from crm_api.schemas.segments import RuleGroup, RuleImpact


class NLToSegmentRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


class LLMSegmentOutput(BaseModel):
    definition: RuleGroup
    rationale: str = Field(min_length=1)


class NLToSegmentResponse(BaseModel):
    definition: dict
    rationale: str
    count: int
    per_rule_impact: list[RuleImpact]
    warnings: list[str]
