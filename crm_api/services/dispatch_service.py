"""Campaign dispatch. Posts queued communications to the channel service in batches.

Dispatch runs inline in the request at demo scale. At higher volume the same
dispatch_campaign interface moves to a background worker, the API shape does not change.
"""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.config import get_settings
from crm_api.models import Campaign, Communication, Customer

BATCH_SIZE = 50


class CampaignNotFoundError(LookupError):
    pass


class InvalidCampaignStateError(RuntimeError):
    pass


class ChannelDispatchError(RuntimeError):
    pass


def resolve_recipient(customer: Customer, channel: str) -> str:
    if channel == "email":
        candidates = (customer.email, customer.phone)
    else:
        candidates = (customer.phone, customer.email)
    for candidate in candidates:
        if candidate:
            return candidate
    return str(customer.id)


async def dispatch_campaign(
    session: AsyncSession, client: httpx.AsyncClient, campaign_id: uuid.UUID
) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise CampaignNotFoundError(str(campaign_id))
    if campaign.status != "draft":
        raise InvalidCampaignStateError(f"campaign status is {campaign.status}, expected draft")

    rows = (
        await session.execute(
            select(Communication, Customer)
            .join(Customer, Customer.id == Communication.customer_id)
            .where(
                Communication.campaign_id == campaign.id,
                Communication.status == "queued",
            )
            .order_by(Communication.created_at, Communication.id)
        )
    ).all()

    campaign.status = "dispatching"
    campaign.dispatched_at = datetime.now(UTC)
    await session.commit()

    send_url = get_settings().channel_send_url
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        payload = {
            "messages": [
                {
                    "communication_id": str(communication.id),
                    "recipient": resolve_recipient(customer, communication.channel),
                    "channel": communication.channel,
                    "body": communication.rendered_message,
                }
                for communication, customer in batch
            ]
        }
        response = await client.post(send_url, json=payload)
        if response.status_code >= 300:
            raise ChannelDispatchError(
                f"channel service returned {response.status_code}, campaign left dispatching"
            )

    campaign.status = "active"
    await session.commit()
    return campaign
