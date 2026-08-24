import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.db import get_session
from crm_api.schemas.scores import DecisionOut, PortfolioSummary
from crm_api.services import scores_service
from crm_api.tenancy import require_tenant

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantDep = Annotated[uuid.UUID, Depends(require_tenant)]


@router.get("/portfolio", response_model=PortfolioSummary)
async def portfolio(session: SessionDep, tenant_id: TenantDep) -> PortfolioSummary:
    return PortfolioSummary(**await scores_service.portfolio_summary(session, tenant_id))


@router.get("/decisions", response_model=list[DecisionOut])
async def decisions(
    session: SessionDep,
    tenant_id: TenantDep,
    tier: str | None = Query(default=None, pattern="^(low|mid|high)$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[DecisionOut]:
    rows = await scores_service.list_decisions(session, tenant_id, tier, limit)
    return [DecisionOut(**row) for row in rows]
