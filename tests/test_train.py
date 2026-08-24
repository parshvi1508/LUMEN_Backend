import numpy as np
import pandas as pd

from ml.features import FEATURE_COLUMNS, LABEL
from ml.train import choose_threshold, evaluate, train_model


def _frame(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.5, size=n) > 0.8).astype(int)
    return pd.DataFrame(
        {
            "recency_days": rng.uniform(1, 400, n),
            "frequency": rng.integers(1, 5, n).astype(float),
            "monetary_total": rng.uniform(50, 1000, n),
            "monetary_avg": rng.uniform(50, 500, n),
            "tenure_days": rng.uniform(1, 700, n),
            "avg_review": rng.uniform(1, 5, n),
            "avg_installments": rng.uniform(1, 10, n),
            "avg_freight_ratio": rng.uniform(0, 0.5, n),
            "avg_delivery_delay": signal,
            "payment_type": np.where(rng.random(n) > 0.5, "credit_card", "boleto"),
            LABEL: label,
        }
    )


def test_evaluate_reports_ranking_and_calibration_not_accuracy() -> None:
    model = train_model(_frame(400, seed=1), max_iter=40)
    metrics = evaluate(model, _frame(200, seed=2))
    assert set(metrics) == {"n", "positive_rate", "pr_auc", "roc_auc", "brier"}
    assert "accuracy" not in metrics
    assert 0.0 <= metrics["pr_auc"] <= 1.0
    assert 0.0 <= metrics["roc_auc"] <= 1.0


def test_training_is_deterministic() -> None:
    test = _frame(200, seed=2)
    m1 = evaluate(train_model(_frame(400, seed=1), max_iter=40), test)
    m2 = evaluate(train_model(_frame(400, seed=1), max_iter=40), test)
    assert m1 == m2


def test_choose_threshold_maximizes_profit_and_is_bounded() -> None:
    y = np.array([1, 1, 0, 0, 1, 0, 0, 0])
    proba = np.array([0.9, 0.8, 0.7, 0.6, 0.55, 0.4, 0.3, 0.1])
    result = choose_threshold(y, proba, value=100.0, cost=1.0)
    assert 0.0 <= result["threshold"] <= 1.0
    assert result["true_positives"] <= result["contacts"]
    assert result["expected_profit"] > 0


def test_feature_columns_present_in_training_frame() -> None:
    frame = _frame(10, seed=3)
    for col in FEATURE_COLUMNS:
        assert col in frame.columns
