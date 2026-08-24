from decimal import Decimal

from crm_api.services.economics import DEFAULT_CHANNEL_COST, campaign_pnl, cost_per_message
from crm_api.services.scores_service import recommend_action


def test_cost_per_message_override_wins() -> None:
    assert cost_per_message("sms", Decimal("0.20")) == Decimal("0.20")


def test_cost_per_message_default_by_channel() -> None:
    assert cost_per_message("email", None) == DEFAULT_CHANNEL_COST["email"]
    assert cost_per_message("unknown", None) == Decimal("0")


def test_campaign_pnl_profit_and_roi() -> None:
    result = campaign_pnl(contacted=100, attributed_revenue=Decimal("500"), unit_cost=Decimal("1"))
    assert result["cost"] == 100.0
    assert result["attributed_revenue"] == 500.0
    assert result["profit"] == 400.0
    assert result["roi"] == 4.0


def test_campaign_pnl_zero_cost_roi_guard() -> None:
    result = campaign_pnl(contacted=0, attributed_revenue=Decimal("0"), unit_cost=Decimal("1"))
    assert result["roi"] == 0.0


def test_recommend_action_rules() -> None:
    assert "win-back" in recommend_action(0.6, "high", 10)
    assert "lapsed" in recommend_action(0.2, "high", 200)
    assert "deprioritize" in recommend_action(0.05, "low", 300)
    assert "Nurture" in recommend_action(0.3, "mid", 30)
