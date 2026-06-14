from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.db import get_session
from crm_api.schemas.customers import PaginatedCustomers, UploadRequest, UploadResult
from crm_api.services import customers_service

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=PaginatedCustomers)
async def list_customers(
    session: SessionDep,
    search: str | None = None,
    page: int = 0,
    page_size: int = Query(20, ge=1, le=500),
) -> PaginatedCustomers:
    return await customers_service.list_customers(session, search, page, page_size)


@router.post("/upload", response_model=UploadResult)
async def upload_customers(payload: UploadRequest, session: SessionDep) -> UploadResult:
    return await customers_service.upload_customers(session, payload)
