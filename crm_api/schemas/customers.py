import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CustomerOut(BaseModel):
    id: uuid.UUID
    external_id: str | None
    name: str
    email: str | None
    city: str | None
    total_spend: float  # float (not Decimal) so the client gets a JS number
    order_count: int
    last_order_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedCustomers(BaseModel):
    data: list[CustomerOut]
    total: int
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    page_count: int = Field(serialization_alias="pageCount")


class UploadRow(BaseModel):
    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    email: str | None = None
    city: str | None = None
    total_spend: Decimal | None = None
    order_count: int | None = None


class UploadRequest(BaseModel):
    rows: list[UploadRow] = Field(min_length=1, max_length=5000)


class RejectedRow(BaseModel):
    row: int
    external_id: str | None = None
    errors: list[str]
    data: dict[str, str] = Field(default_factory=dict)


class UploadResult(BaseModel):
    created: int
    updated: int
    rejected: list[RejectedRow] = Field(default_factory=list)
