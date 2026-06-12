import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal["sent", "delivered", "failed", "opened", "read", "clicked", "converted"]


class ReceiptEvent(BaseModel):
    communication_id: uuid.UUID
    event_id: str = Field(min_length=1)
    event_type: EventType
    occurred_at: datetime


class ReceiptBatch(BaseModel):
    events: list[ReceiptEvent] = Field(min_length=1, max_length=1000)


class ReceiptEventResult(BaseModel):
    communication_id: uuid.UUID
    event_type: EventType
    result: Literal["accepted", "duplicate", "unknown_communication"]


class ReceiptResponse(BaseModel):
    results: list[ReceiptEventResult]
