from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class CustomerIn(BaseModel):
    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class OrderIn(BaseModel):
    external_id: str = Field(min_length=1)
    customer_external_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    items: list[Any] = Field(default_factory=list)
    ordered_at: datetime


class BulkCustomersRequest(BaseModel):
    customers: list[CustomerIn] = Field(min_length=1, max_length=1000)


class BulkOrdersRequest(BaseModel):
    orders: list[OrderIn] = Field(min_length=1, max_length=1000)


class IngestError(BaseModel):
    external_id: str
    reason: str


class IngestResult(BaseModel):
    inserted: int
    updated: int = 0
    skipped_duplicates: int = 0
    errors: list[IngestError] = Field(default_factory=list)
