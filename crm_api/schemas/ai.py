import uuid
from typing import Literal

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


class DraftMessagesRequest(BaseModel):
    campaign_intent: str = Field(min_length=1, max_length=2000)
    segment_id: uuid.UUID
    channel: Literal["whatsapp", "sms", "email"]


class MessageVariant(BaseModel):
    variant: str = Field(min_length=1)
    message: str = Field(min_length=1)
    tone: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)


class LLMDraftOutput(BaseModel):
    variants: list[MessageVariant] = Field(min_length=1, max_length=5)


class DraftMessagesResponse(BaseModel):
    segment_id: uuid.UUID
    channel: str
    variants: list[MessageVariant]


class InsightFact(BaseModel):
    label: str
    value: int


class LLMInsightOutput(BaseModel):
    narrative: str = Field(min_length=1)


class InsightResponse(BaseModel):
    campaign_id: uuid.UUID
    narrative: str
    facts: list[InsightFact]


class ProposeCampaignRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)


class ProposalSegment(BaseModel):
    definition: RuleGroup
    rationale: str = Field(min_length=1)


class LLMProposalOutput(BaseModel):
    segment: ProposalSegment
    recommended_channel: Literal["whatsapp", "sms", "email"]
    channel_reasoning: str = Field(min_length=1)
    variants: list[MessageVariant] = Field(min_length=1, max_length=5)


class ProposeCampaignResponse(BaseModel):
    campaign_id: uuid.UUID | None = None  # proposal is not persisted until approved
    proposal_state: str
    goal: str
    segment_definition: dict
    segment_rationale: str
    audience_size: int | None
    recommended_channel: str
    channel_reasoning: str
    variants: list[MessageVariant]
