"""Campaign economics. Pure functions, no DB, so the P&L math is unit-tested in
isolation and reused by the serving layer.

Per-message send costs are business inputs, not data. They default to documented
placeholder rates and are overridden per campaign via campaigns.cost_per_message.
"""

from decimal import Decimal

DEFAULT_CHANNEL_COST = {
    "sms": Decimal("0.05"),
    "whatsapp": Decimal("0.07"),
    "email": Decimal("0.01"),
}


def cost_per_message(channel: str | None, override: Decimal | None) -> Decimal:
    if override is not None:
        return override
    return DEFAULT_CHANNEL_COST.get(channel or "", Decimal("0"))


def _money(value: Decimal) -> float:
    return float(round(value, 2))


def campaign_pnl(contacted: int, attributed_revenue: Decimal, unit_cost: Decimal) -> dict:
    cost = unit_cost * contacted
    profit = attributed_revenue - cost
    roi = float(profit / cost) if cost > 0 else 0.0
    return {
        "contacted": contacted,
        "cost": _money(cost),
        "attributed_revenue": _money(attributed_revenue),
        "profit": _money(profit),
        "roi": round(roi, 4),
    }
