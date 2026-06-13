from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.db import get_session
from crm_api.http_client import get_http_client
from crm_api.schemas.ai import (
    DraftMessagesRequest,
    DraftMessagesResponse,
    NLToSegmentRequest,
    NLToSegmentResponse,
)
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


@router.post("/draft-messages", response_model=DraftMessagesResponse)
async def draft_messages(
    payload: DraftMessagesRequest, session: SessionDep, client: ClientDep
) -> DraftMessagesResponse:
    try:
        return await ai_service.draft_messages(session, client, payload)
    except ai_service.SegmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="segment not found") from exc
    except ai_service.DraftGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail="llm providers unavailable") from exc
