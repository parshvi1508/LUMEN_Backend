"""Tenant-scoped reads over customer_scores: the portfolio money view and the
per-customer decision layer (score to reason to action to expected impact).
"""

import uuid
from decimal import Decimal

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Campaign, Communication, Customer, CustomerScore, Order
from crm_api.services import economics

LAPSED_DAYS = 180
LIKELY_THRESHOLD = 0.5
UNLIKELY_THRESHOLD = 0.1


def recommend_action(probability: float, value_tier: str, recency_days: int | None) -> str:
    lapsed = recency_days is not None and recency_days > LAPSED_DAYS
    if value_tier == "high" and probability >= LIKELY_THRESHOLD:
        return "Send a win-back offer now, high value and likely to return"
    if value_tier == "high" and lapsed:
        return "High-value but lapsed, prioritize personal outreach"
    if probability < UNLIKELY_THRESHOLD:
        return "Low reactivation likelihood, deprioritize paid contact"
    return "Nurture with a light-touch campaign"


async def portfolio_summary(session: AsyncSession, tenant_id: uuid.UUID) -> dict:
    zero = cast(0, Numeric(12, 2))
    row = (
        await session.execute(
            select(
                func.count().label("n"),
                func.coalesce(func.sum(CustomerScore.expected_value), zero).label("expected"),
                func.coalesce(
                    func.sum(
                        case(
                            (CustomerScore.value_tier == "high", CustomerScore.expected_value),
                            else_=zero,
                        )
                    ),
                    zero,
                ).label("opportunity"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                CustomerScore.recency_days > LAPSED_DAYS,
                                CustomerScore.monetary_total,
                            ),
                            else_=zero,
                        )
                    ),
                    zero,
                ).label("at_risk"),
            ).where(CustomerScore.tenant_id == tenant_id)
        )
    ).one()

    tier_rows = (
        await session.execute(
            select(CustomerScore.value_tier, func.count())
            .where(CustomerScore.tenant_id == tenant_id)
            .group_by(CustomerScore.value_tier)
        )
    ).all()

    return {
        "customers_scored": int(row.n),
        "portfolio_expected_value": float(row.expected),
        "reactivation_opportunity_high_tier": float(row.opportunity),
        "revenue_at_risk": float(row.at_risk),
        "tier_counts": {tier: int(count) for tier, count in tier_rows},
    }


async def list_decisions(
    session: AsyncSession, tenant_id: uuid.UUID, tier: str | None, limit: int
) -> list[dict]:
    stmt = (
        select(CustomerScore, Customer.name)
        .join(Customer, Customer.id == CustomerScore.customer_id)
        .where(CustomerScore.tenant_id == tenant_id)
        .order_by(CustomerScore.expected_value.desc())
        .limit(limit)
    )
    if tier is not None:
        stmt = stmt.where(CustomerScore.value_tier == tier)

    decisions = []
    for score, name in (await session.execute(stmt)).all():
        probability = float(score.reactivation_probability)
        decisions.append(
            {
                "customer_id": score.customer_id,
                "name": name,
                "reactivation_probability": probability,
                "expected_value": float(score.expected_value),
                "value_tier": score.value_tier,
                "recency_days": score.recency_days,
                "recommended_action": recommend_action(
                    probability, score.value_tier, score.recency_days
                ),
                "reasons": score.reasons,
            }
        )
    return decisions


async def campaign_pnl(
    session: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> dict | None:
    campaign = (
        await session.execute(
            select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if campaign is None:
        return None

    contacted = await session.scalar(
        select(func.count(Communication.id)).where(Communication.campaign_id == campaign_id)
    )
    attributed_revenue = await session.scalar(
        select(func.coalesce(func.sum(Order.amount), cast(0, Numeric(12, 2)))).where(
            Order.attributed_campaign_id == campaign_id
        )
    )
    unit_cost = economics.cost_per_message(campaign.channel, campaign.cost_per_message)
    pnl = economics.campaign_pnl(int(contacted or 0), Decimal(attributed_revenue), unit_cost)
    return {"campaign_id": campaign_id, **pnl}
