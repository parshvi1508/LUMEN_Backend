import hashlib
import json
import re
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Campaign, Communication, Customer, Order, Segment
from crm_api.schemas.campaigns import CampaignCreate, WinBackRequest
from crm_api.schemas.segments import RuleGroup, RuleLeaf
from crm_api.services import dispatch_service, segment_compiler

TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


class SegmentNotFoundError(LookupError):
    pass


class CampaignNotFoundError(LookupError):
    pass


class NotAProposalError(RuntimeError):
    pass


class ProposalNotApprovedError(RuntimeError):
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
    last_order_at = (
        f"{customer.last_order_at.day} {customer.last_order_at.strftime('%b')}"
        if customer.last_order_at
        else None
    )
    return {
        "name": customer.name,
        "first_name": customer.name.split()[0] if customer.name else "",
        "city": customer.city,
        "total_spend": customer.total_spend,
        "last_order_amount": last_order_amount,
        "last_order_at": last_order_at,
    }


async def create_campaign(
    session: AsyncSession,
    payload: CampaignCreate,
    tenant_id: uuid.UUID | None = None,
) -> Campaign:
    segment = await session.get(Segment, payload.segment_id)
    if segment is None:
        raise SegmentNotFoundError(str(payload.segment_id))

    definition = RuleGroup.model_validate(segment.definition)
    where = segment_compiler.compile_definition(definition)
    if tenant_id is not None:
        where = where & (Customer.tenant_id == tenant_id)

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
        tenant_id=tenant_id,
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
                    "tenant_id": tenant_id,
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


LAPSED_DAYS = 180
TIER_PERCENTILE = {"high": 0.75, "mid": 0.40, "low": 0.0}


def _definition_hash(definition: dict) -> str:
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def create_winback_campaign(
    session: AsyncSession, tenant_id: uuid.UUID, payload: WinBackRequest
) -> Campaign:
    rules: list[RuleGroup | RuleLeaf] = [
        RuleLeaf(field="last_order_at", cmp="older_than_days", value=LAPSED_DAYS),
    ]

    if payload.tier is not None:
        pct = TIER_PERCENTILE[payload.tier]
        if pct > 0:
            threshold = await session.scalar(
                select(
                    func.percentile_cont(pct).within_group(Customer.total_spend)
                ).where(Customer.tenant_id == tenant_id)
            )
            if threshold is not None and float(threshold) > 0:
                rules.append(
                    RuleLeaf(
                        field="total_spend",
                        cmp="gte",
                        value=round(float(threshold), 2),
                    )
                )

    definition = RuleGroup(op="AND", rules=rules)
    def_dict = definition.model_dump()
    dhash = _definition_hash(def_dict)

    segment = (
        await session.execute(
            select(Segment).where(
                Segment.tenant_id == tenant_id,
                Segment.definition_hash == dhash,
            )
        )
    ).scalar_one_or_none()

    if segment is None:
        segment = Segment(
            tenant_id=tenant_id,
            name=f"Win-back lapsed {payload.tier or 'all'}",
            definition=def_dict,
            definition_hash=dhash,
            source="ai",
            ai_rationale="Auto-generated win-back segment targeting lapsed customers",
        )
        session.add(segment)
        await session.flush()

    create_payload = CampaignCreate(
        name=payload.name,
        segment_id=segment.id,
        channel=payload.channel,
        message_template=payload.message_template,
    )
    return await create_campaign(session, create_payload, tenant_id=tenant_id)


async def queued_count(session: AsyncSession, campaign_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count(Communication.id)).where(
            Communication.campaign_id == campaign_id, Communication.status == "queued"
        )
    )


def _proposal_state(campaign: Campaign) -> str:
    reasoning = campaign.ai_reasoning or {}
    state = reasoning.get("proposal_state")
    if state is None:
        raise NotAProposalError(str(campaign.id))
    return state


async def approve_proposal(session: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise CampaignNotFoundError(str(campaign_id))
    _proposal_state(campaign)
    campaign.ai_reasoning = {**campaign.ai_reasoning, "proposal_state": "approved"}
    await session.commit()
    return campaign


async def execute_proposal(
    session: AsyncSession, client: httpx.AsyncClient, campaign_id: uuid.UUID
) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise CampaignNotFoundError(str(campaign_id))
    if _proposal_state(campaign) != "approved":
        raise ProposalNotApprovedError(str(campaign_id))
    return await dispatch_service.dispatch_campaign(session, client, campaign_id)
