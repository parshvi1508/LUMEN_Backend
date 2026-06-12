import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select

from crm_api.models import Campaign, Communication, Customer, Order, Segment
from crm_api.services.receipt_service import SIMULATED_CONVERSION_AMOUNT
from tests.test_receipts import event, post_receipts


@pytest_asyncio.fixture
async def attribution_env(db_session):
    customer = Customer(
        name="Attribution Tester",
        external_id=f"attr_{uuid.uuid4().hex[:8]}",
        email="attr@test.local",
    )
    segment = Segment(name="attr seg", definition={"op": "AND", "rules": []}, source="manual")
    db_session.add_all([customer, segment])
    await db_session.flush()
    campaign = Campaign(
        name="attr camp",
        segment_id=segment.id,
        channel="email",
        message_template="hi",
        status="active",
        audience_size=1,
    )
    db_session.add(campaign)
    await db_session.flush()
    communication = Communication(
        campaign_id=campaign.id,
        customer_id=customer.id,
        channel="email",
        rendered_message="hi",
        status="queued",
        status_rank=0,
    )
    db_session.add(communication)
    await db_session.flush()
    return customer, campaign, communication


async def orders_for(db_session, customer_id) -> list[Order]:
    return list(await db_session.scalars(select(Order).where(Order.customer_id == customer_id)))


async def aggregates_of(db_session, customer_id) -> tuple[Decimal, int, datetime | None]:
    row = (
        await db_session.execute(
            select(Customer.total_spend, Customer.order_count, Customer.last_order_at).where(
                Customer.id == customer_id
            )
        )
    ).one()
    return tuple(row)


async def test_converted_creates_attributed_order(client, db_session, attribution_env) -> None:
    customer, campaign, communication = attribution_env
    occurred = datetime.now(UTC)

    resp = await post_receipts(client, [event(communication.id, "converted", occurred)])
    assert resp.status_code == 200
    assert resp.json()["results"][0]["result"] == "accepted"

    orders = await orders_for(db_session, customer.id)
    assert len(orders) == 1
    order = orders[0]
    assert order.attributed_campaign_id == campaign.id
    assert order.customer_id == customer.id
    assert order.amount == SIMULATED_CONVERSION_AMOUNT
    assert order.ordered_at == occurred
    assert order.external_id == f"conv_{communication.id}"

    spend, count, last_order_at = await aggregates_of(db_session, customer.id)
    assert spend == SIMULATED_CONVERSION_AMOUNT
    assert count == 1
    assert last_order_at == occurred


async def test_duplicate_converted_creates_one_order(client, db_session, attribution_env) -> None:
    customer, _, communication = attribution_env
    converted = event(communication.id, "converted")

    first = await post_receipts(client, [converted])
    assert first.status_code == 200
    second = await post_receipts(client, [converted])
    assert second.status_code == 200
    assert second.json()["results"][0]["result"] == "duplicate"

    assert len(await orders_for(db_session, customer.id)) == 1
    spend, count, _ = await aggregates_of(db_session, customer.id)
    assert spend == SIMULATED_CONVERSION_AMOUNT
    assert count == 1


async def test_retry_storm_one_order(client, db_session, attribution_env) -> None:
    customer, _, communication = attribution_env
    batch = [event(communication.id, "sent"), event(communication.id, "converted")]

    for _ in range(3):
        resp = await post_receipts(client, batch)
        assert resp.status_code == 200

    assert len(await orders_for(db_session, customer.id)) == 1
    spend, count, _ = await aggregates_of(db_session, customer.id)
    assert spend == SIMULATED_CONVERSION_AMOUNT
    assert count == 1


async def test_out_of_order_converted_then_delivered(client, db_session, attribution_env) -> None:
    customer, _, communication = attribution_env

    resp = await post_receipts(client, [event(communication.id, "converted")])
    assert resp.status_code == 200
    late = await post_receipts(client, [event(communication.id, "delivered")])
    assert late.status_code == 200

    status, rank = (
        await db_session.execute(
            select(Communication.status, Communication.status_rank).where(
                Communication.id == communication.id
            )
        )
    ).one()
    assert (status, rank) == ("converted", 60)
    assert len(await orders_for(db_session, customer.id)) == 1


async def test_unknown_communication_converted_no_order(client, db_session) -> None:
    before = await db_session.scalar(select(func.count()).select_from(Order))

    resp = await post_receipts(client, [event(uuid.uuid4(), "converted")])
    assert resp.status_code == 200
    assert resp.json()["results"][0]["result"] == "unknown_communication"

    after = await db_session.scalar(select(func.count()).select_from(Order))
    assert after == before


async def test_non_converted_events_create_no_order(client, db_session, attribution_env) -> None:
    customer, _, communication = attribution_env

    resp = await post_receipts(
        client, [event(communication.id, "sent"), event(communication.id, "delivered")]
    )
    assert resp.status_code == 200

    assert await orders_for(db_session, customer.id) == []
    spend, count, _ = await aggregates_of(db_session, customer.id)
    assert spend == 0
    assert count == 0
