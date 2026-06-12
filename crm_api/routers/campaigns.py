import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.db import get_session
from crm_api.models import Campaign
from crm_api.schemas.campaigns import CampaignCreate, CampaignOut
from crm_api.services import campaign_service
from crm_api.services.segment_compiler import SegmentCompileError

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=CampaignOut, status_code=201)
async def create_campaign(payload: CampaignCreate, session: SessionDep) -> Campaign:
    try:
        return await campaign_service.create_campaign(session, payload)
    except campaign_service.SegmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="segment not found") from exc
    except SegmentCompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: uuid.UUID, session: SessionDep) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    return campaign
