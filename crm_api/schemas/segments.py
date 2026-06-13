import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class RuleLeaf(BaseModel):
    field: str
    cmp: str
    value: Any = None


class RuleGroup(BaseModel):
    op: Literal["AND", "OR"]
    rules: list["RuleGroup | RuleLeaf"] = Field(min_length=1)


class PreviewRequest(BaseModel):
    definition: RuleGroup


class SegmentCreate(BaseModel):
    name: str = Field(min_length=1)
    definition: RuleGroup
    source: Literal["manual", "ai"] = "manual"
    ai_rationale: str | None = None


class SegmentOut(BaseModel):
    id: uuid.UUID
    name: str
    definition: dict
    source: str | None
    ai_rationale: str | None
    created_at: datetime


class CustomerSample(BaseModel):
    id: uuid.UUID
    external_id: str | None
    name: str
    city: str | None
    total_spend: Decimal
    last_order_at: datetime | None


class RuleImpact(BaseModel):
    rule: str
    path: list[int]           # index path to the leaf, disambiguates duplicate labels
    count: int                # standalone match count (this rule alone)
    audience_without: int     # full-segment audience if this leaf were removed
    marginal: int             # audience_without - audience_with_all_rules


class PreviewResponse(BaseModel):
    count: int
    sample: list[CustomerSample]
    per_rule_impact: list[RuleImpact]
