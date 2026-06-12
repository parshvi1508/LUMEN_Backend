"""Seed the database with realistic demo data for a D2C coffee brand.

Run: venv\\Scripts\\python -m scripts.seed
Idempotent: deterministic external_ids, customers upsert, orders skip on conflict.
"""

import asyncio
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from faker import Faker

from crm_api.db import get_sessionmaker
from crm_api.schemas.ingest import (
    BulkCustomersRequest,
    BulkOrdersRequest,
    CustomerIn,
    OrderIn,
)
from crm_api.services import ingest_service

SEED = 42
TOTAL_CUSTOMERS = 600
BATCH_SIZE = 500

COHORTS = [
    ("loyalist", 90, (8, 15), (80000, 300000)),
    ("lapsed", 150, (2, 5), (25000, 120000)),
    ("one_time", 120, (1, 1), (20000, 80000)),
    ("regular", 240, (2, 5), (25000, 150000)),
]

CITIES = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Pune"]

SKUS = [
    ("BEAN-AR-250", "Araku Valley Arabica 250g"),
    ("BEAN-MO-250", "Monsooned Malabar 250g"),
    ("POD-ESP-10", "Espresso Pods 10 pack"),
    ("COLD-BREW-6", "Cold Brew Cans 6 pack"),
    ("EQ-FRENCH", "French Press 600ml"),
    ("EQ-POUR", "Pour Over Kit"),
]

FESTIVE_MONTHS = {10, 11, 12}


def sample_days_ago(rng: random.Random, now: datetime, min_days: int, max_days: int) -> int:
    """Uniform sample with festive months (Oct to Dec) weighted double."""
    candidates = [rng.randint(min_days, max_days) for _ in range(2)]
    first = candidates[0]
    if (now - timedelta(days=first)).month in FESTIVE_MONTHS:
        return first
    second = candidates[1]
    if (now - timedelta(days=second)).month in FESTIVE_MONTHS:
        return second
    return first


def make_customers() -> list[CustomerIn]:
    fake = Faker("en_IN")
    Faker.seed(SEED)
    rng = random.Random(SEED)
    customers: list[CustomerIn] = []
    idx = 0
    for cohort, count, _, _ in COHORTS:
        for _ in range(count):
            idx += 1
            customers.append(
                CustomerIn(
                    external_id=f"cust_{idx:04d}",
                    name=fake.name(),
                    email=fake.email(),
                    phone=fake.phone_number(),
                    city=rng.choice(CITIES),
                    attributes={"cohort": cohort, "source": "seed"},
                )
            )
    return customers


def make_orders(customers: list[CustomerIn], now: datetime | None = None) -> list[OrderIn]:
    now = now or datetime.now(UTC)
    rng = random.Random(SEED + 1)
    cohort_spec = {name: (orders, paise) for name, _, orders, paise in COHORTS}
    orders: list[OrderIn] = []
    order_idx = 0
    for customer in customers:
        cohort = customer.attributes["cohort"]
        order_range, paise_range = cohort_spec[cohort]
        n_orders = rng.randint(*order_range)
        for i in range(n_orders):
            order_idx += 1
            if cohort == "lapsed":
                days_ago = rng.randint(91, 365)
            elif cohort == "loyalist" and i == 0:
                days_ago = rng.randint(0, 30)
            else:
                days_ago = sample_days_ago(rng, now, 0, 365)
            n_items = rng.randint(1, 3)
            items = [
                {"sku": sku, "name": name, "qty": rng.randint(1, 3)}
                for sku, name in rng.sample(SKUS, n_items)
            ]
            orders.append(
                OrderIn(
                    external_id=f"ord_{order_idx:05d}",
                    customer_external_id=customer.external_id,
                    amount=Decimal(rng.randint(*paise_range)) / 100,
                    items=items,
                    ordered_at=now - timedelta(days=days_ago, minutes=rng.randint(0, 1439)),
                )
            )
    return orders


async def main() -> None:
    customers = make_customers()
    orders = make_orders(customers)

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        total_c = {"inserted": 0, "updated": 0}
        for i in range(0, len(customers), BATCH_SIZE):
            batch = customers[i : i + BATCH_SIZE]
            result = await ingest_service.ingest_customers(
                session, BulkCustomersRequest(customers=batch)
            )
            total_c["inserted"] += result.inserted
            total_c["updated"] += result.updated

        total_o = {"inserted": 0, "skipped": 0, "errors": 0}
        for i in range(0, len(orders), BATCH_SIZE):
            batch = orders[i : i + BATCH_SIZE]
            result = await ingest_service.ingest_orders(session, BulkOrdersRequest(orders=batch))
            total_o["inserted"] += result.inserted
            total_o["skipped"] += result.skipped_duplicates
            total_o["errors"] += len(result.errors)

        print(f"customers: {total_c}")
        print(f"orders: {total_o}")

        from sqlalchemy import select

        from crm_api.models import Customer

        sample = await session.scalars(
            select(Customer).order_by(Customer.total_spend.desc()).limit(3)
        )
        for c in sample:
            print(
                f"sample: {c.external_id} {c.name} spend={c.total_spend} "
                f"orders={c.order_count} last={c.last_order_at:%Y-%m-%d}"
            )


if __name__ == "__main__":
    asyncio.run(main())
