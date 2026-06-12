from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.db import get_session
from crm_api.models import Customer
from crm_api.schemas.segments import (
    CustomerSample,
    PreviewRequest,
    PreviewResponse,
    RuleImpact,
)
from crm_api.services import segment_compiler

router = APIRouter(prefix="/api/v1/segments", tags=["segments"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/preview", response_model=PreviewResponse)
async def preview(payload: PreviewRequest, session: SessionDep) -> PreviewResponse:
    try:
        where = segment_compiler.compile_definition(payload.definition)
        leaves = segment_compiler.collect_leaves(payload.definition)
        leaf_filters = [
            (segment_compiler.leaf_label(leaf), segment_compiler.compile_leaf(leaf))
            for leaf in leaves
        ]
    except segment_compiler.SegmentCompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    count = await session.scalar(select(func.count(Customer.id)).where(where))
    sample_rows = await session.scalars(
        select(Customer).where(where).order_by(Customer.total_spend.desc()).limit(10)
    )
    sample = [CustomerSample.model_validate(c, from_attributes=True) for c in sample_rows]

    impacts = []
    for label, leaf_where in leaf_filters:
        leaf_count = await session.scalar(select(func.count(Customer.id)).where(leaf_where))
        impacts.append(RuleImpact(rule=label, count=leaf_count))

    return PreviewResponse(count=count, sample=sample, per_rule_impact=impacts)
