from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.db import get_session
from crm_api.http_client import get_http_client
from crm_api.schemas.ai import NLToSegmentRequest, NLToSegmentResponse
from crm_api.services import ai_service
from crm_api.services.llm_client import LLMUnavailableError

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


@router.post("/nl-to-segment", response_model=NLToSegmentResponse)
async def nl_to_segment(
    payload: NLToSegmentRequest, session: SessionDep, client: ClientDep
) -> NLToSegmentResponse:
    try:
        return await ai_service.nl_to_segment(session, client, payload.prompt)
    except ai_service.SegmentGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail="llm providers unavailable") from exc
