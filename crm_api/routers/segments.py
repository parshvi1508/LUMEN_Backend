import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.db import get_session
from crm_api.models import Customer, Segment
from crm_api.schemas.segments import (
    CustomerSample,
    PreviewRequest,
    PreviewResponse,
    SegmentCreate,
    SegmentOut,
)
from crm_api.services import segment_compiler, segment_preview

router = APIRouter(prefix="/api/v1/segments", tags=["segments"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/preview", response_model=PreviewResponse)
async def preview(payload: PreviewRequest, session: SessionDep) -> PreviewResponse:
    try:
        where = segment_compiler.compile_definition(payload.definition)
    except segment_compiler.SegmentCompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    count = await session.scalar(select(func.count(Customer.id)).where(where))
    sample_rows = await session.scalars(
        select(Customer).where(where).order_by(Customer.total_spend.desc()).limit(10)
    )
    sample = [CustomerSample.model_validate(c, from_attributes=True) for c in sample_rows]

    impacts = await segment_preview.collect_rule_impacts(session, payload.definition, count)
    return PreviewResponse(count=count, sample=sample, per_rule_impact=impacts)


@router.post("", response_model=SegmentOut, status_code=201)
async def create_segment(payload: SegmentCreate, session: SessionDep) -> Segment:
    try:
        segment_compiler.compile_definition(payload.definition)
    except segment_compiler.SegmentCompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    segment = Segment(
        name=payload.name,
        definition=payload.definition.model_dump(),
        source=payload.source,
        ai_rationale=payload.ai_rationale,
    )
    session.add(segment)
    await session.commit()
    return segment


@router.get("", response_model=list[SegmentOut])
async def list_segments(session: SessionDep) -> list[Segment]:
    rows = await session.scalars(select(Segment).order_by(Segment.created_at.desc()))
    return list(rows)


@router.get("/{segment_id}", response_model=SegmentOut)
async def get_segment(segment_id: uuid.UUID, session: SessionDep) -> Segment:
    segment = await session.get(Segment, segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail="segment not found")
    return segment
