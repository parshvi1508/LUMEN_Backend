import re
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Campaign, Communication, Customer, Order, Segment
from crm_api.schemas.campaigns import CampaignCreate
from crm_api.schemas.segments import RuleGroup
from crm_api.services import segment_compiler

TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


class SegmentNotFoundError(LookupError):
    pass


def render_message(template: str, fields: dict[str, object]) -> str:
    def replace(match: re.Match) -> str:
        token = match.group(1)
        if token not in fields:
            return match.group(0)
        value = fields[token]
        return "" if value is None else str(value)

    return TOKEN_RE.sub(replace, template)


def customer_fields(customer: Customer, last_order_amount: Decimal | None) -> dict[str, object]:
    return {
        "name": customer.name,
        "first_name": customer.name.split()[0] if customer.name else "",
        "city": customer.city,
        "total_spend": customer.total_spend,
        "last_order_amount": last_order_amount,
    }


async def create_campaign(session: AsyncSession, payload: CampaignCreate) -> Campaign:
    segment = await session.get(Segment, payload.segment_id)
    if segment is None:
        raise SegmentNotFoundError(str(payload.segment_id))

    definition = RuleGroup.model_validate(segment.definition)
    where = segment_compiler.compile_definition(definition)

    latest_order = (
        select(Order.customer_id, Order.amount)
        .distinct(Order.customer_id)
        .order_by(Order.customer_id, Order.ordered_at.desc())
        .subquery()
    )
    rows = await session.execute(
        select(Customer, latest_order.c.amount)
        .outerjoin(latest_order, latest_order.c.customer_id == Customer.id)
        .where(where)
    )
    audience = rows.all()

    campaign = Campaign(
        name=payload.name,
        segment_id=segment.id,
        channel=payload.channel,
        message_template=payload.message_template,
        status="draft",
        audience_size=len(audience),
    )
    session.add(campaign)
    await session.flush()

    if audience:
        await session.execute(
            Communication.__table__.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "campaign_id": campaign.id,
                    "customer_id": customer.id,
                    "channel": payload.channel,
                    "rendered_message": render_message(
                        payload.message_template, customer_fields(customer, amount)
                    ),
                }
                for customer, amount in audience
            ],
        )
    await session.commit()
    return campaign


async def queued_count(session: AsyncSession, campaign_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count(Communication.id)).where(
            Communication.campaign_id == campaign_id, Communication.status == "queued"
        )
    )
