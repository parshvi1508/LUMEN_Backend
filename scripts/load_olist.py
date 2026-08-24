"""Load Olist customers, orders, and ML scores into the tenant's tables.

Olist is anonymized, so display names are synthesized deterministically from the
customer key (documented, not real identities). Idempotent: customers and orders
upsert on external_id, scores upsert on (tenant_id, customer_id). Run after
alembic upgrade head and after a tenant row exists.

Run: venv\\Scripts\\python -m scripts.load_olist
"""

import asyncio
import json
from decimal import Decimal

import pandas as pd
from faker import Faker
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from crm_api.db import get_sessionmaker
from crm_api.models import Customer, CustomerScore, Order, Tenant
from ml.config import ARTIFACTS_DIR, CSV_FILES, RAW_DIR
from ml.dataset import order_level

BATCH = 1000
SCORES_PATH = ARTIFACTS_DIR / "customer_scores.parquet"


def _synth_names(keys: list[str]) -> dict[str, str]:
    fake = Faker("pt_BR")
    Faker.seed(42)
    return {key: fake.name() for key in sorted(keys)}


def _customer_rows(orders: pd.DataFrame, tenant_id) -> list[dict]:
    priced = orders.dropna(subset=["customer_unique_id", "order_value"])
    agg = priced.groupby("customer_unique_id").agg(
        total_spend=("order_value", "sum"),
        order_count=("order_id", "nunique"),
        last_order_at=("order_purchase_timestamp", "max"),
    )

    raw = pd.read_csv(RAW_DIR / CSV_FILES["customers"])
    geo = raw.groupby("customer_unique_id")[["customer_city", "customer_state"]].first()

    names = _synth_names(agg.index.tolist())
    rows = []
    for cuid, row in agg.iterrows():
        city = geo["customer_city"].get(cuid)
        state = geo["customer_state"].get(cuid)
        rows.append(
            {
                "tenant_id": tenant_id,
                "external_id": cuid,
                "name": names[cuid],
                "city": None if pd.isna(city) else str(city),
                "attributes": {"state": None if pd.isna(state) else str(state), "source": "olist"},
                "total_spend": Decimal(str(round(float(row["total_spend"]), 2))),
                "order_count": int(row["order_count"]),
                "last_order_at": row["last_order_at"].to_pydatetime(),
            }
        )
    return rows


def _order_rows(orders: pd.DataFrame, tenant_id, id_by_external: dict[str, object]) -> list[dict]:
    priced = orders.dropna(subset=["customer_unique_id", "order_value"]).drop_duplicates("order_id")
    rows = []
    for _, o in priced.iterrows():
        customer_id = id_by_external.get(o["customer_unique_id"])
        if customer_id is None:
            continue
        rows.append(
            {
                "tenant_id": tenant_id,
                "external_id": o["order_id"],
                "customer_id": customer_id,
                "amount": Decimal(str(round(float(o["order_value"]), 2))),
                "ordered_at": o["order_purchase_timestamp"].to_pydatetime(),
            }
        )
    return rows


def _score_rows(tenant_id, id_by_external: dict[str, object]) -> list[dict]:
    scores = pd.read_parquet(SCORES_PATH)
    scores["scored_at"] = pd.to_datetime(scores["scored_at"], utc=True)
    rows = []
    for _, s in scores.iterrows():
        customer_id = id_by_external.get(s["customer_unique_id"])
        if customer_id is None:
            continue
        rows.append(
            {
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "reactivation_probability": Decimal(
                    str(round(float(s["reactivation_probability"]), 6))
                ),
                "expected_value": Decimal(str(round(float(s["expected_value"]), 2))),
                "value_tier": str(s["value_tier"]),
                "recency_days": int(s["recency_days"]),
                "frequency": int(s["frequency"]),
                "monetary_total": Decimal(str(round(float(s["monetary_total"]), 2))),
                "reasons": json.loads(s["reasons"]),
                "model_version": str(s["model_version"]),
                "scored_at": s["scored_at"].to_pydatetime(),
            }
        )
    return rows


async def _upsert(
    session, table, rows: list[dict], index_elements: list[str], update_cols: list[str]
):
    for start in range(0, len(rows), BATCH):
        chunk = rows[start : start + BATCH]
        stmt = insert(table).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_={col: getattr(stmt.excluded, col) for col in update_cols},
        )
        await session.execute(stmt)
    await session.commit()


async def main() -> None:
    orders = order_level()
    orders["order_purchase_timestamp"] = orders["order_purchase_timestamp"].dt.tz_localize("UTC")

    async with get_sessionmaker()() as session:
        tenant_id = await session.scalar(select(Tenant.id).order_by(Tenant.created_at).limit(1))
        if tenant_id is None:
            raise SystemExit("No tenant row found. Create one before loading.")

        customers = _customer_rows(orders, tenant_id)
        await _upsert(
            session,
            Customer,
            customers,
            ["external_id"],
            [
                "tenant_id",
                "name",
                "city",
                "attributes",
                "total_spend",
                "order_count",
                "last_order_at",
            ],
        )

        id_by_external = dict(
            (
                await session.execute(
                    select(Customer.external_id, Customer.id).where(Customer.tenant_id == tenant_id)
                )
            ).all()
        )

        order_rows = _order_rows(orders, tenant_id, id_by_external)
        await _upsert(
            session,
            Order,
            order_rows,
            ["external_id"],
            ["tenant_id", "customer_id", "amount", "ordered_at"],
        )

        score_rows = _score_rows(tenant_id, id_by_external)
        await _upsert(
            session,
            CustomerScore,
            score_rows,
            ["tenant_id", "customer_id"],
            [
                "reactivation_probability",
                "expected_value",
                "value_tier",
                "recency_days",
                "frequency",
                "monetary_total",
                "reasons",
                "model_version",
                "scored_at",
            ],
        )

        print(
            json.dumps(
                {
                    "tenant_id": str(tenant_id),
                    "customers": len(customers),
                    "orders": len(order_rows),
                    "scores": len(score_rows),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
