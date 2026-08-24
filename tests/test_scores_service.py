import uuid
from datetime import UTC, datetime
from decimal import Decimal

from crm_api.models import Campaign, Communication, Customer, CustomerScore, Order, Tenant
from crm_api.services import scores_service


async def test_portfolio_and_decisions_are_tenant_scoped(db_session) -> None:
    tid = uuid.uuid4()
    db_session.add(Tenant(id=tid, name="t"))
    await db_session.flush()
    alice = Customer(id=uuid.uuid4(), tenant_id=tid, name="Alice", external_id=f"a-{tid}")
    bob = Customer(id=uuid.uuid4(), tenant_id=tid, name="Bob", external_id=f"b-{tid}")
    db_session.add_all([alice, bob])
    await db_session.flush()

    db_session.add_all(
        [
            CustomerScore(
                tenant_id=tid,
                customer_id=alice.id,
                reactivation_probability=Decimal("0.6"),
                expected_value=Decimal("300"),
                value_tier="high",
                recency_days=10,
                frequency=3,
                monetary_total=Decimal("900"),
                reasons=[{"feature": "recency_days", "impact": 0.5, "direction": "increases"}],
                model_version="v",
                scored_at=datetime.now(UTC),
            ),
            CustomerScore(
                tenant_id=tid,
                customer_id=bob.id,
                reactivation_probability=Decimal("0.05"),
                expected_value=Decimal("10"),
                value_tier="low",
                recency_days=400,
                frequency=1,
                monetary_total=Decimal("50"),
                reasons=[],
                model_version="v",
                scored_at=datetime.now(UTC),
            ),
        ]
    )
    await db_session.flush()

    summary = await scores_service.portfolio_summary(db_session, tid)
    assert summary["customers_scored"] == 2
    assert summary["portfolio_expected_value"] == 310.0
    assert summary["reactivation_opportunity_high_tier"] == 300.0
    assert summary["revenue_at_risk"] == 50.0
    assert summary["tier_counts"] == {"high": 1, "low": 1}

    decisions = await scores_service.list_decisions(db_session, tid, tier=None, limit=50)
    assert decisions[0]["name"] == "Alice"
    assert "win-back" in decisions[0]["recommended_action"]

    other_tenant = await scores_service.portfolio_summary(db_session, uuid.uuid4())
    assert other_tenant["customers_scored"] == 0


async def test_campaign_pnl_uses_real_attributed_revenue(db_session) -> None:
    tid = uuid.uuid4()
    db_session.add(Tenant(id=tid, name="t"))
    await db_session.flush()
    cust = Customer(id=uuid.uuid4(), tenant_id=tid, name="X", external_id=f"x-{tid}")
    db_session.add(cust)
    await db_session.flush()

    camp = Campaign(
        id=uuid.uuid4(),
        tenant_id=tid,
        name="c",
        message_template="hi",
        channel="email",
        status="active",
        cost_per_message=Decimal("1"),
    )
    db_session.add(camp)
    await db_session.flush()

    db_session.add(
        Communication(
            tenant_id=tid,
            campaign_id=camp.id,
            customer_id=cust.id,
            channel="email",
            rendered_message="hi",
        )
    )
    db_session.add(
        Order(
            tenant_id=tid,
            customer_id=cust.id,
            external_id=f"o-{tid}",
            amount=Decimal("200"),
            ordered_at=datetime.now(UTC),
            attributed_campaign_id=camp.id,
        )
    )
    await db_session.flush()

    pnl = await scores_service.campaign_pnl(db_session, tid, camp.id)
    assert pnl["contacted"] == 1
    assert pnl["attributed_revenue"] == 200.0
    assert pnl["cost"] == 1.0
    assert pnl["profit"] == 199.0


async def test_campaign_pnl_unknown_returns_none(db_session) -> None:
    result = await scores_service.campaign_pnl(db_session, uuid.uuid4(), uuid.uuid4())
    assert result is None
