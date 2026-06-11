from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.db import get_session
from crm_api.schemas.ingest import BulkCustomersRequest, BulkOrdersRequest, IngestResult
from crm_api.services import ingest_service

router = APIRouter(prefix="/api/v1", tags=["ingest"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/customers/bulk", response_model=IngestResult)
async def bulk_customers(payload: BulkCustomersRequest, session: SessionDep) -> IngestResult:
    return await ingest_service.ingest_customers(session, payload)


@router.post("/orders/bulk", response_model=IngestResult)
async def bulk_orders(payload: BulkOrdersRequest, session: SessionDep) -> IngestResult:
    return await ingest_service.ingest_orders(session, payload)
