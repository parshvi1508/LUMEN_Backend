"""Exploratory data analysis for the Olist dataset.

Runs on-device, no notebook. Produces named figures in ml/figures and a
machine-readable ml/eda_stats.json. Every number in the observations report is
sourced from this run, so nothing is assumed.

Run: venv\\Scripts\\python -m ml.eda
"""

import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ml.config import CSV_FILES, FIGURES_DIR, RAW_DIR, STATS_PATH

matplotlib.use("Agg")

DELIVERED = "delivered"


def _read(name: str, parse_dates: list[str] | None = None) -> pd.DataFrame:
    return pd.read_csv(RAW_DIR / CSV_FILES[name], parse_dates=parse_dates)


def load_frames() -> dict[str, pd.DataFrame]:
    return {
        "customers": _read("customers"),
        "orders": _read(
            "orders",
            parse_dates=["order_purchase_timestamp", "order_delivered_customer_date"],
        ),
        "order_items": _read("order_items"),
        "order_payments": _read("order_payments"),
        "order_reviews": _read("order_reviews"),
        "products": _read("products"),
        "sellers": _read("sellers"),
        "category_translation": _read("category_translation"),
    }


def order_values(order_items: pd.DataFrame) -> pd.DataFrame:
    """Order value = sum of item price plus freight, one row per order_id."""
    grouped = order_items.groupby("order_id", as_index=False).agg(
        item_revenue=("price", "sum"),
        freight=("freight_value", "sum"),
    )
    grouped["order_value"] = grouped["item_revenue"] + grouped["freight"]
    return grouped


def save_fig(fig: plt.Figure, name: str) -> str:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURES_DIR / name
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return str(path.relative_to(Path(__file__).resolve().parent))


def _pct(series: pd.Series, points: tuple[int, ...]) -> dict[str, float]:
    return {f"p{p}": round(float(np.percentile(series, p)), 2) for p in points}


def analyze(frames: dict[str, pd.DataFrame]) -> dict:
    stats: dict = {}
    orders = frames["orders"]
    customers = frames["customers"]
    items = frames["order_items"]

    stats["row_counts"] = {name: int(len(df)) for name, df in frames.items()}

    status_counts = orders["order_status"].value_counts()
    stats["order_status"] = {k: int(v) for k, v in status_counts.items()}

    purchase = orders["order_purchase_timestamp"]
    stats["date_range"] = {
        "min": str(purchase.min()),
        "max": str(purchase.max()),
        "days": int((purchase.max() - purchase.min()).days),
    }

    fig, ax = plt.subplots(figsize=(9, 4))
    monthly = orders.set_index("order_purchase_timestamp").resample("MS").size()
    ax.plot(monthly.index, monthly.values, color="#2563eb")
    ax.set_title("Monthly order volume")
    ax.set_xlabel("Month")
    ax.set_ylabel("Orders")
    save_fig(fig, "monthly_order_volume.png")

    fig, ax = plt.subplots(figsize=(7, 4))
    status_counts.plot.bar(ax=ax, color="#0ea5e9")
    ax.set_title("Order status distribution")
    ax.set_ylabel("Orders")
    save_fig(fig, "order_status_distribution.png")

    delivered = orders[orders["order_status"] == DELIVERED].copy()
    values = order_values(items)
    delivered = delivered.merge(values[["order_id", "order_value"]], on="order_id", how="inner")
    delivered = delivered.merge(
        customers[["customer_id", "customer_unique_id"]], on="customer_id", how="left"
    )

    stats["delivered_orders"] = int(len(delivered))
    stats["revenue_total"] = round(float(delivered["order_value"].sum()), 2)
    stats["order_value"] = {
        "mean": round(float(delivered["order_value"].mean()), 2),
        **_pct(delivered["order_value"], (10, 50, 90, 99)),
    }

    fig, ax = plt.subplots(figsize=(7, 4))
    p99_value = float(np.percentile(delivered["order_value"], 99))
    clipped = delivered["order_value"].clip(upper=p99_value)
    ax.hist(clipped, bins=50, color="#22c55e")
    ax.set_title("Order value distribution (clipped at p99)")
    ax.set_xlabel("Order value (BRL)")
    ax.set_ylabel("Orders")
    save_fig(fig, "order_value_distribution.png")

    per_customer = delivered.groupby("customer_unique_id").agg(
        n_orders=("order_id", "nunique"),
        total_spend=("order_value", "sum"),
        first_purchase=("order_purchase_timestamp", "min"),
        last_purchase=("order_purchase_timestamp", "max"),
    )
    n_customers = int(len(per_customer))
    repeat = per_customer["n_orders"] > 1
    stats["customers"] = {
        "unique": n_customers,
        "repeat": int(repeat.sum()),
        "repeat_rate": round(float(repeat.mean()), 4),
        "one_time_rate": round(float((~repeat).mean()), 4),
    }
    stats["clv_proxy_total_spend"] = {
        "mean": round(float(per_customer["total_spend"].mean()), 2),
        **_pct(per_customer["total_spend"], (50, 90, 99)),
    }

    orders_dist = per_customer["n_orders"].value_counts().sort_index()
    fig, ax = plt.subplots(figsize=(7, 4))
    head = orders_dist.head(8)
    ax.bar(head.index.astype(str), head.values, color="#8b5cf6")
    ax.set_title("Orders per customer")
    ax.set_xlabel("Number of delivered orders")
    ax.set_ylabel("Customers")
    save_fig(fig, "orders_per_customer.png")

    snapshot = delivered["order_purchase_timestamp"].max()
    recency_days = (snapshot - per_customer["last_purchase"]).dt.days
    stats["recency_days"] = {
        "snapshot": str(snapshot),
        "mean": round(float(recency_days.mean()), 1),
        **_pct(recency_days, (10, 50, 90)),
    }
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(recency_days, bins=40, color="#f59e0b")
    ax.set_title("Customer recency (days since last order)")
    ax.set_xlabel("Days")
    ax.set_ylabel("Customers")
    save_fig(fig, "recency_days_distribution.png")

    repeat_orders = delivered[
        delivered["customer_unique_id"].isin(per_customer[repeat].index)
    ].sort_values(["customer_unique_id", "order_purchase_timestamp"])
    gaps = (
        repeat_orders.groupby("customer_unique_id")["order_purchase_timestamp"]
        .diff()
        .dropna()
        .dt.days
    )
    if len(gaps):
        stats["interpurchase_gap_days"] = {
            "count": int(len(gaps)),
            "mean": round(float(gaps.mean()), 1),
            **_pct(gaps, (50, 90)),
        }
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.hist(gaps, bins=40, color="#ef4444")
        ax.set_title("Inter-purchase gap for repeat customers")
        ax.set_xlabel("Days between consecutive orders")
        ax.set_ylabel("Order pairs")
        save_fig(fig, "interpurchase_gap_days.png")

    reviews = frames["order_reviews"]
    review_counts = reviews["review_score"].value_counts().sort_index()
    stats["review_score"] = {
        "mean": round(float(reviews["review_score"].mean()), 3),
        "distribution": {int(k): int(v) for k, v in review_counts.items()},
    }
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(review_counts.index.astype(str), review_counts.values, color="#14b8a6")
    ax.set_title("Review score distribution")
    ax.set_xlabel("Score")
    ax.set_ylabel("Reviews")
    save_fig(fig, "review_score_distribution.png")

    payments = frames["order_payments"]
    pay_counts = payments["payment_type"].value_counts()
    stats["payment_type"] = {k: int(v) for k, v in pay_counts.items()}
    stats["installments"] = {
        "mean": round(float(payments["payment_installments"].mean()), 2),
        "max": int(payments["payment_installments"].max()),
    }

    products = frames["products"]
    translation = frames["category_translation"]
    cat = items.merge(
        products[["product_id", "product_category_name"]], on="product_id", how="left"
    )
    cat = cat.merge(translation, on="product_category_name", how="left")
    cat["category"] = cat["product_category_name_english"].fillna(cat["product_category_name"])
    top_categories = cat["category"].value_counts().head(10)
    stats["top_categories"] = {str(k): int(v) for k, v in top_categories.items()}
    fig, ax = plt.subplots(figsize=(8, 5))
    top_categories.sort_values().plot.barh(ax=ax, color="#6366f1")
    ax.set_title("Top 10 categories by item count")
    ax.set_xlabel("Items sold")
    save_fig(fig, "top_categories.png")

    seller_rev = (
        items.assign(value=items["price"] + items["freight_value"])
        .groupby("seller_id")["value"]
        .sum()
        .sort_values(ascending=False)
    )
    total_rev = float(seller_rev.sum())
    cum_share = seller_rev.cumsum() / total_rev
    sellers_for_80 = int((cum_share <= 0.80).sum() + 1)
    stats["sellers"] = {
        "count": int(len(seller_rev)),
        "top1_revenue_share": round(float(seller_rev.iloc[0] / total_rev), 4),
        "top10_revenue_share": round(float(seller_rev.head(10).sum() / total_rev), 4),
        "sellers_for_80pct_revenue": sellers_for_80,
        "sellers_for_80pct_share": round(sellers_for_80 / len(seller_rev), 4),
    }
    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(1, len(cum_share) + 1) / len(cum_share)
    ax.plot(x, cum_share.values, color="#db2777")
    ax.axhline(0.8, color="#9ca3af", linestyle="--", linewidth=1)
    ax.set_title("Seller revenue concentration (Lorenz curve)")
    ax.set_xlabel("Cumulative share of sellers")
    ax.set_ylabel("Cumulative share of revenue")
    save_fig(fig, "seller_revenue_concentration.png")

    return stats


def main() -> None:
    frames = load_frames()
    stats = analyze(frames)
    STATS_PATH.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"wrote {STATS_PATH}")
    print(f"figures in {FIGURES_DIR}")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
