"""Single order-level loader shared by every ML stage, so feature definitions
never diverge between EDA, training, and serving.
"""

import pandas as pd

from ml.config import CSV_FILES, RAW_DIR

_ORDER_DATES = [
    "order_purchase_timestamp",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
]


def _read(name: str, parse_dates: list[str] | None = None) -> pd.DataFrame:
    return pd.read_csv(RAW_DIR / CSV_FILES[name], parse_dates=parse_dates)


def _order_values(items: pd.DataFrame) -> pd.DataFrame:
    grouped = items.groupby("order_id", as_index=False).agg(
        item_revenue=("price", "sum"),
        freight=("freight_value", "sum"),
    )
    grouped["order_value"] = grouped["item_revenue"] + grouped["freight"]
    grouped["freight_ratio"] = (grouped["freight"] / grouped["order_value"]).where(
        grouped["order_value"] > 0
    )
    return grouped[["order_id", "order_value", "freight_ratio"]]


def _order_reviews(reviews: pd.DataFrame) -> pd.DataFrame:
    agg = reviews.groupby("order_id", as_index=False).agg(
        review_score=("review_score", "mean"),
        review_comment_message=("review_comment_message", lambda s: " ".join(
            s.dropna().astype(str)
        )),
    )
    agg.loc[agg["review_comment_message"].str.strip() == "", "review_comment_message"] = ""
    return agg


def _mode(series: pd.Series) -> str:
    modes = series.mode()
    return str(modes.iat[0]) if not modes.empty else "unknown"


def _order_payments(payments: pd.DataFrame) -> pd.DataFrame:
    return payments.groupby("order_id", as_index=False).agg(
        installments=("payment_installments", "max"),
        payment_type=("payment_type", _mode),
    )


def order_level() -> pd.DataFrame:
    """One row per order enriched with the real customer key and per-order signals."""
    orders = _read("orders", parse_dates=_ORDER_DATES)
    customers = _read("customers")
    items = _read("order_items")
    reviews = _read("order_reviews")
    payments = _read("order_payments")

    df = orders.merge(
        customers[["customer_id", "customer_unique_id"]], on="customer_id", how="left"
    )
    df = df.merge(_order_values(items), on="order_id", how="left")
    df = df.merge(_order_reviews(reviews), on="order_id", how="left")
    df = df.merge(_order_payments(payments), on="order_id", how="left")
    df["delivery_delay_days"] = (
        df["order_delivered_customer_date"] - df["order_estimated_delivery_date"]
    ).dt.days

    return df[
        [
            "order_id",
            "customer_unique_id",
            "order_purchase_timestamp",
            "order_status",
            "order_value",
            "freight_ratio",
            "review_score",
            "review_comment_message",
            "installments",
            "payment_type",
            "delivery_delay_days",
        ]
    ]
