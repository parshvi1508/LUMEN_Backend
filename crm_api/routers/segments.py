import hashlib
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.db import get_session
from crm_api.models import Campaign, Customer, Segment
from crm_api.schemas.segments import (
    CustomerSample,
    PreviewRequest,
    PreviewResponse,
    SegmentCreate,
    SegmentOut,
)
from crm_api.services import segment_compiler, segment_preview


def _definition_hash(definition: dict) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()

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
async def create_segment(
    payload: SegmentCreate, session: SessionDep, response: Response
) -> Segment:
    try:
        segment_compiler.compile_definition(payload.definition)
    except segment_compiler.SegmentCompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    defn_dict = payload.definition.model_dump()
    defn_hash = _definition_hash(defn_dict)
    rows = await session.scalars(select(Segment))
    for existing in rows:
        if _definition_hash(existing.definition) == defn_hash:
            response.status_code = 200
            return existing

    segment = Segment(
        name=payload.name.strip(),
        definition=defn_dict,
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


@router.delete("/{segment_id}", status_code=204)
async def delete_segment(segment_id: uuid.UUID, session: SessionDep) -> Response:
    segment = await session.get(Segment, segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail="segment not found")
    # Unlink any campaigns that referenced it (FK is nullable) so the delete
    # does not violate the foreign key.
    await session.execute(
        update(Campaign).where(Campaign.segment_id == segment_id).values(segment_id=None)
    )
    await session.delete(segment)
    await session.commit()
    return Response(status_code=204)
