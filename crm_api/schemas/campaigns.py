import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1)
    segment_id: uuid.UUID
    channel: Literal["whatsapp", "sms", "email"]
    message_template: str = Field(min_length=1)


class FunnelStep(BaseModel):
    status: str
    rank: int
    count: int


class CampaignStats(BaseModel):
    campaign_id: uuid.UUID
    campaign_status: str | None
    audience_size: int | None
    total: int
    funnel: list[FunnelStep]
    failed: int
    failure_rate: float
    converted: int
    dispatched_at: datetime | None


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    segment_id: uuid.UUID | None
    channel: str | None
    message_template: str
    status: str | None
    audience_size: int | None
    created_at: datetime
    dispatched_at: datetime | None
