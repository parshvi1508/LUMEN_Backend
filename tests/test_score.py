import numpy as np
import pandas as pd

from ml.features import FEATURE_COLUMNS, LABEL
from ml.score import calibrate, compute_reasons
from ml.train import train_model


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


def test_calibrated_probabilities_are_bounded() -> None:
    pipeline = train_model(_frame(400, seed=1), max_iter=40)
    calibrated = calibrate(pipeline, _frame(200, seed=2))
    proba = calibrated.predict_proba(_frame(100, seed=3)[FEATURE_COLUMNS])[:, 1]
    assert proba.min() >= 0.0
    assert proba.max() <= 1.0


def test_reasons_are_capped_and_well_formed() -> None:
    pipeline = train_model(_frame(400, seed=1), max_iter=40)
    frame = _frame(20, seed=4)
    reasons = compute_reasons(pipeline, frame, top_k=3)
    assert len(reasons) == len(frame)
    for row in reasons:
        assert 1 <= len(row) <= 3
        for reason in row:
            assert set(reason) == {"feature", "impact", "direction"}
            assert reason["direction"] in {"increases", "decreases"}
            assert reason["feature"] in FEATURE_COLUMNS
