import uuid
from typing import Literal

from pydantic import BaseModel, Field


class SendMessage(BaseModel):
    communication_id: uuid.UUID
    recipient: str = Field(min_length=1)
    channel: Literal["whatsapp", "sms", "email"]
    body: str = Field(min_length=1)


class SendBatch(BaseModel):
    messages: list[SendMessage] = Field(min_length=1, max_length=1000)


class SendAccepted(BaseModel):
    accepted: int
