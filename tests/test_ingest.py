from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select

from crm_api.models import Customer, Order

CUSTOMERS = [
    {"external_id": "c1", "name": "Asha Rao", "email": "asha@example.com", "city": "Mumbai"},
    {"external_id": "c2", "name": "Vikram Mehta", "city": "Delhi"},
]

ORDERS = [
    {
        "external_id": "o1",
        "customer_external_id": "c1",
        "amount": "100.50",
        "ordered_at": "2026-01-05T10:00:00Z",
    },
    {
        "external_id": "o2",
        "customer_external_id": "c1",
        "amount": "200.25",
        "ordered_at": "2026-03-10T12:00:00Z",
    },
    {
        "external_id": "o3",
        "customer_external_id": "c2",
        "amount": "99.99",
        "ordered_at": "2026-02-01T09:00:00Z",
    },
]


async def seed_customers(client) -> None:
    resp = await client.post("/api/v1/customers/bulk", json={"customers": CUSTOMERS})
    assert resp.status_code == 200


async def test_customers_upsert(client, db_session) -> None:
    await seed_customers(client)

    changed = [dict(CUSTOMERS[0], name="Asha R"), CUSTOMERS[1]]
    resp = await client.post("/api/v1/customers/bulk", json={"customers": changed})
    assert resp.status_code == 200
    assert resp.json() == {"inserted": 0, "updated": 2, "skipped_duplicates": 0, "errors": []}

    count = await db_session.scalar(
        select(func.count(Customer.id)).where(Customer.external_id.in_(["c1", "c2"]))
    )
    assert count == 2
    c1 = await db_session.scalar(select(Customer).where(Customer.external_id == "c1"))
    assert c1.name == "Asha R"
    assert c1.total_spend == Decimal("0.00")
    assert c1.order_count == 0


async def test_order_aggregates_hand_computed(client, db_session) -> None:
    await seed_customers(client)

    resp = await client.post("/api/v1/orders/bulk", json={"orders": ORDERS})
    assert resp.status_code == 200
    assert resp.json() == {"inserted": 3, "updated": 0, "skipped_duplicates": 0, "errors": []}

    c1 = await db_session.scalar(select(Customer).where(Customer.external_id == "c1"))
    assert c1.total_spend == Decimal("300.75")
    assert c1.order_count == 2
    assert c1.last_order_at == datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

    c2 = await db_session.scalar(select(Customer).where(Customer.external_id == "c2"))
    assert c2.total_spend == Decimal("99.99")
    assert c2.order_count == 1
    assert c2.last_order_at == datetime(2026, 2, 1, 9, 0, tzinfo=UTC)


async def test_order_reingest_safe(client, db_session) -> None:
    await seed_customers(client)
    await client.post("/api/v1/orders/bulk", json={"orders": ORDERS})

    resp = await client.post("/api/v1/orders/bulk", json={"orders": ORDERS})
    assert resp.json() == {"inserted": 0, "updated": 0, "skipped_duplicates": 3, "errors": []}

    c1 = await db_session.scalar(select(Customer).where(Customer.external_id == "c1"))
    assert c1.total_spend == Decimal("300.75")
    assert c1.order_count == 2
    order_count = await db_session.scalar(
        select(func.count(Order.id)).where(Order.external_id.in_(["o1", "o2", "o3"]))
    )
    assert order_count == 3


async def test_unknown_customer_reported(client, db_session) -> None:
    await seed_customers(client)

    orders = [
        ORDERS[0],
        dict(ORDERS[2], external_id="o9", customer_external_id="ghost"),
    ]
    resp = await client.post("/api/v1/orders/bulk", json={"orders": orders})
    body = resp.json()
    assert body["inserted"] == 1
    assert body["errors"] == [{"external_id": "o9", "reason": "unknown customer_external_id"}]


async def test_validation_rejects_bad_rows(client) -> None:
    resp = await client.post(
        "/api/v1/orders/bulk",
        json={
            "orders": [
                {
                    "external_id": "ox",
                    "customer_external_id": "c1",
                    "amount": "-5.00",
                    "ordered_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    )
    assert resp.status_code == 422

    resp = await client.post("/api/v1/customers/bulk", json={"customers": [{"name": "No Id"}]})
    assert resp.status_code == 422
