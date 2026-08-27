"""Leak-safe feature construction for the reactivation-propensity model.

All features for a customer are computed only from orders placed on or before the
cutoff. The label looks strictly forward, into (cutoff, cutoff + horizon]. A
customer with no order on or before the cutoff is not in the population, so no
future information can enter a feature.
"""

import pandas as pd

NUMERIC_FEATURES = [
    "recency_days",
    "frequency",
    "monetary_total",
    "monetary_avg",
    "tenure_days",
    "avg_review",
    "avg_installments",
    "avg_freight_ratio",
    "avg_delivery_delay",
    "review_word_count",
    "review_text_ratio",
]
CATEGORICAL_FEATURES = ["payment_type"]
TEXT_FEATURES = ["review_text"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES + TEXT_FEATURES
LABEL = "label"
KEY = "customer_unique_id"


def _word_count(text: str) -> int:
    if not text or not isinstance(text, str):
        return 0
    return len(text.split())


def build_features(orders: pd.DataFrame, cutoff: str, horizon_days: int) -> pd.DataFrame:
    cutoff_ts = pd.Timestamp(cutoff)
    horizon_end = cutoff_ts + pd.Timedelta(days=horizon_days)
    ts = orders["order_purchase_timestamp"]

    past = orders[ts <= cutoff_ts]
    future = orders[(ts > cutoff_ts) & (ts <= horizon_end)]

    grouped = past.groupby(KEY)
    feat = grouped.agg(
        frequency=("order_id", "nunique"),
        monetary_total=("order_value", "sum"),
        monetary_avg=("order_value", "mean"),
        first_purchase=("order_purchase_timestamp", "min"),
        last_purchase=("order_purchase_timestamp", "max"),
        avg_review=("review_score", "mean"),
        avg_installments=("installments", "mean"),
        avg_freight_ratio=("freight_ratio", "mean"),
        avg_delivery_delay=("delivery_delay_days", "mean"),
    )
    feat["recency_days"] = (cutoff_ts - feat["last_purchase"]).dt.days
    feat["tenure_days"] = (cutoff_ts - feat["first_purchase"]).dt.days
    feat["payment_type"] = grouped["payment_type"].agg(
        lambda s: s.mode().iat[0] if not s.mode().empty else "unknown"
    )

    text_agg = past.groupby(KEY).agg(
        review_text=(
            "review_comment_message",
            lambda s: " ".join(s.fillna("").astype(str)),
        ),
        _has_text=(
            "review_comment_message",
            lambda s: (s.fillna("").astype(str).str.len() > 0).sum(),
        ),
        _total_reviews=("review_comment_message", "count"),
    )
    feat["review_text"] = text_agg["review_text"].fillna("")
    feat["review_word_count"] = feat["review_text"].apply(_word_count).astype(float)
    denom = text_agg["_total_reviews"].replace(0, 1)
    feat["review_text_ratio"] = (text_agg["_has_text"] / denom).fillna(0)

    returners = set(future[KEY].unique())
    feat[LABEL] = feat.index.isin(returners).astype(int)

    return feat.drop(columns=["first_purchase", "last_purchase"]).reset_index()
