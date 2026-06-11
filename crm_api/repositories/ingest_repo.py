import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Customer, Order
from crm_api.schemas.ingest import CustomerIn, OrderIn


async def upsert_customers(session: AsyncSession, customers: list[CustomerIn]) -> tuple[int, int]:
    existing = await session.scalars(
        select(Customer.external_id).where(
            Customer.external_id.in_([c.external_id for c in customers])
        )
    )
    existing_ids = set(existing)

    stmt = insert(Customer).values([c.model_dump() for c in customers])
    stmt = stmt.on_conflict_do_update(
        index_elements=[Customer.external_id],
        set_={
            "name": stmt.excluded.name,
            "email": stmt.excluded.email,
            "phone": stmt.excluded.phone,
            "city": stmt.excluded.city,
            "attributes": stmt.excluded.attributes,
        },
    )
    await session.execute(stmt)
    updated = len(existing_ids)
    return len(customers) - updated, updated


async def resolve_customer_ids(
    session: AsyncSession, external_ids: list[str]
) -> dict[str, uuid.UUID]:
    rows = await session.execute(
        select(Customer.external_id, Customer.id).where(Customer.external_id.in_(external_ids))
    )
    return dict(rows.all())


async def insert_orders(
    session: AsyncSession, orders: list[OrderIn], customer_ids: dict[str, uuid.UUID]
) -> list[tuple[uuid.UUID, Decimal, datetime]]:
    """Insert orders, skipping external_ids already present. Returns rows actually inserted."""
    values = [
        {
            "external_id": o.external_id,
            "customer_id": customer_ids[o.customer_external_id],
            "amount": o.amount,
            "items": o.items,
            "ordered_at": o.ordered_at,
        }
        for o in orders
    ]
    stmt = (
        insert(Order)
        .values(values)
        .on_conflict_do_nothing(index_elements=[Order.external_id])
        .returning(Order.customer_id, Order.amount, Order.ordered_at)
    )
    result = await session.execute(stmt)
    return [tuple(row) for row in result.all()]


async def apply_order_aggregates(
    session: AsyncSession, inserted: list[tuple[uuid.UUID, Decimal, datetime]]
) -> None:
    per_customer: dict[uuid.UUID, dict] = {}
    for customer_id, amount, ordered_at in inserted:
        agg = per_customer.setdefault(
            customer_id, {"spend": Decimal("0"), "count": 0, "latest": ordered_at}
        )
        agg["spend"] += amount
        agg["count"] += 1
        agg["latest"] = max(agg["latest"], ordered_at)

    for customer_id, agg in per_customer.items():
        await session.execute(
            update(Customer)
            .where(Customer.id == customer_id)
            .values(
                total_spend=Customer.total_spend + agg["spend"],
                order_count=Customer.order_count + agg["count"],
                last_order_at=func.greatest(
                    func.coalesce(Customer.last_order_at, agg["latest"]), agg["latest"]
                ),
            )
        )
