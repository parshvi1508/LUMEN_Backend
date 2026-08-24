import numpy as np
import pandas as pd

from ml.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    KEY,
    LABEL,
    NUMERIC_FEATURES,
    build_features,
)
from ml.split import make_preprocessor, stratified_split

CUTOFF = "2018-03-01"
HORIZON = 180


def _order(cust: str, oid: str, ts: str, value: float) -> dict:
    return {
        KEY: cust,
        "order_id": oid,
        "order_purchase_timestamp": pd.Timestamp(ts),
        "order_value": value,
        "review_score": 5.0,
        "installments": 1,
        "freight_ratio": 0.1,
        "delivery_delay_days": -2.0,
        "payment_type": "credit_card",
    }


def _orders() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _order("C1", "o1", "2018-01-15", 100.0),  # past
            _order("C1", "o2", "2018-04-10", 200.0),  # future, in window -> label 1
            _order("C2", "o3", "2018-02-01", 50.0),  # past only -> label 0
            _order("C3", "o4", "2018-05-01", 80.0),  # no past order -> excluded
            _order("C4", "o5", "2017-12-01", 70.0),  # past
            _order("C4", "o6", "2018-10-01", 90.0),  # future but AFTER horizon -> label 0
        ]
    )


def test_population_excludes_customers_without_past_orders() -> None:
    feat = build_features(_orders(), CUTOFF, HORIZON)
    assert set(feat[KEY]) == {"C1", "C2", "C4"}


def test_labels_look_only_forward_within_horizon() -> None:
    feat = build_features(_orders(), CUTOFF, HORIZON).set_index(KEY)
    assert feat.loc["C1", LABEL] == 1
    assert feat.loc["C2", LABEL] == 0
    assert feat.loc["C4", LABEL] == 0


def test_features_do_not_leak_future_orders() -> None:
    feat = build_features(_orders(), CUTOFF, HORIZON).set_index(KEY)
    # C1's second order is after the cutoff, so it must not count toward as-of features.
    assert feat.loc["C1", "frequency"] == 1
    assert feat.loc["C1", "monetary_total"] == 100.0
    assert feat.loc["C1", "recency_days"] == 45


def _labeled_frame(n: int, positives: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rows.append(
            {
                KEY: f"cust_{i}",
                "recency_days": float(rng.integers(1, 400)),
                "frequency": float(rng.integers(1, 5)),
                "monetary_total": float(rng.uniform(50, 1000)),
                "monetary_avg": float(rng.uniform(50, 500)),
                "tenure_days": float(rng.integers(1, 700)),
                "avg_review": float(rng.uniform(1, 5)),
                "avg_installments": float(rng.uniform(1, 10)),
                "avg_freight_ratio": float(rng.uniform(0, 0.5)),
                "avg_delivery_delay": float(rng.uniform(-10, 10)),
                "payment_type": "credit_card" if i % 2 else "boleto",
                LABEL: 1 if i < positives else 0,
            }
        )
    return pd.DataFrame(rows)


def test_split_has_no_customer_overlap_and_covers_all() -> None:
    frame = _labeled_frame(30, positives=9)
    train, val, test = stratified_split(frame)
    keys = [set(part[KEY]) for part in (train, val, test)]
    assert keys[0].isdisjoint(keys[1])
    assert keys[0].isdisjoint(keys[2])
    assert keys[1].isdisjoint(keys[2])
    assert keys[0] | keys[1] | keys[2] == set(frame[KEY])


def test_preprocessor_fits_on_train_and_handles_unseen_category() -> None:
    frame = _labeled_frame(30, positives=9)
    train, val, _ = stratified_split(frame)
    pre = make_preprocessor()
    pre.fit(train[FEATURE_COLUMNS])
    # An unseen category at transform time must not raise (handle_unknown="ignore").
    val = val.copy()
    val.loc[val.index[0], CATEGORICAL_FEATURES[0]] = "unseen_type"
    transformed = pre.transform(val[FEATURE_COLUMNS])
    assert transformed.shape[0] == len(val)
    assert transformed.shape[1] == len(pre.get_feature_names_out())
    assert len(NUMERIC_FEATURES) == 9
