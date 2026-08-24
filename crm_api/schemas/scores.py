import uuid

from pydantic import BaseModel


class Reason(BaseModel):
    feature: str
    impact: float
    direction: str


class DecisionOut(BaseModel):
    customer_id: uuid.UUID
    name: str
    reactivation_probability: float
    expected_value: float
    value_tier: str
    recency_days: int | None
    recommended_action: str
    reasons: list[Reason]


class PortfolioSummary(BaseModel):
    customers_scored: int
    portfolio_expected_value: float
    reactivation_opportunity_high_tier: float
    revenue_at_risk: float
    revenue_leakage: float
    lapsed_count: int
    avg_expected_value: float
    tier_counts: dict[str, int]


class CampaignPnl(BaseModel):
    campaign_id: uuid.UUID
    contacted: int
    cost: float
    attributed_revenue: float
    profit: float
    roi: float
