import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Campaign, Communication
from crm_api.schemas.campaigns import CampaignStats, FunnelStep
from crm_api.services.receipt_service import STATUS_RANKS


class CampaignNotFoundError(Exception):
    pass


async def campaign_stats(session: AsyncSession, campaign_id: uuid.UUID) -> CampaignStats:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise CampaignNotFoundError(str(campaign_id))

    rows = await session.execute(
        select(Communication.status, func.count())
        .where(Communication.campaign_id == campaign_id)
        .group_by(Communication.status)
    )
    counts: dict[str, int] = dict(rows.all())

    funnel = [
        FunnelStep(status=status, rank=rank, count=counts.get(status, 0))
        for status, rank in sorted(STATUS_RANKS.items(), key=lambda item: item[1])
    ]
    total = sum(step.count for step in funnel)
    failed = counts.get("failed", 0)
    return CampaignStats(
        campaign_id=campaign.id,
        campaign_status=campaign.status,
        audience_size=campaign.audience_size,
        total=total,
        funnel=funnel,
        failed=failed,
        failure_rate=round(failed / total, 4) if total else 0.0,
        converted=counts.get("converted", 0),
        dispatched_at=campaign.dispatched_at,
    )
