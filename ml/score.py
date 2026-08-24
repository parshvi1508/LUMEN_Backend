"""Batch scoring: calibrated reactivation probability, expected-value CLV, and
per-customer SHAP reasons, written to a scores table the serving layer reads.

Probabilities from the class-weighted ranker are not calibrated, so the expected
-impact math would be wrong. Here the model is calibrated with isotonic
regression fit on validation only, then applied to every customer. CLV is an
honest expected value (calibrated probability times the customer's average order
value); a BG/NBD lifetimes model is not used because a 97 percent one-time base
does not identify its repeat-rate parameters.
"""

import json
from datetime import UTC, datetime

import joblib
import numpy as np
import pandas as pd
import shap
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import brier_score_loss

from ml.config import ARTIFACTS_DIR, PROCESSED_DIR
from ml.dataset import order_level
from ml.features import CATEGORICAL_FEATURES, FEATURE_COLUMNS, LABEL, build_features

SCORES_PATH = ARTIFACTS_DIR / "customer_scores.parquet"
SCORING_MANIFEST_PATH = ARTIFACTS_DIR / "scoring_manifest.json"
TOP_REASONS = 3


def load_model() -> object:
    return joblib.load(ARTIFACTS_DIR / "model_latest.joblib")


def calibrate(pipeline: object, val: pd.DataFrame) -> CalibratedClassifierCV:
    # FrozenEstimator keeps the trained pipeline fixed; only the isotonic
    # calibrator is fit, on validation, so training data never leaks into it.
    calibrated = CalibratedClassifierCV(FrozenEstimator(pipeline), method="isotonic")
    calibrated.fit(val[FEATURE_COLUMNS], val[LABEL])
    return calibrated


def scoring_cutoff(orders: pd.DataFrame) -> str:
    return orders["order_purchase_timestamp"].max().strftime("%Y-%m-%d")


def build_scoring_features(orders: pd.DataFrame, cutoff: str) -> pd.DataFrame:
    feat = build_features(orders, cutoff, horizon_days=0)
    return feat.drop(columns=[LABEL])


def _base_feature(name: str) -> str:
    stripped = name.split("__", 1)[-1]
    for cat in CATEGORICAL_FEATURES:
        if stripped == cat or stripped.startswith(cat + "_"):
            return cat
    return stripped


def compute_reasons(
    pipeline: object, frame: pd.DataFrame, top_k: int = TOP_REASONS
) -> list[list[dict]]:
    """Top-k drivers per customer, aggregated to base features so a one-hot
    category reads as one reason rather than one per level.
    """
    transformed = pipeline["pre"].transform(frame[FEATURE_COLUMNS])
    bases = [_base_feature(n) for n in pipeline["pre"].get_feature_names_out()]
    unique_bases = list(dict.fromkeys(bases))
    columns_by_base = {b: [i for i, bb in enumerate(bases) if bb == b] for b in unique_bases}

    explainer = shap.TreeExplainer(pipeline["clf"])
    values = np.asarray(explainer.shap_values(transformed))
    reasons: list[list[dict]] = []
    for row in values:
        aggregated = {b: float(row[cols].sum()) for b, cols in columns_by_base.items()}
        top = sorted(aggregated.items(), key=lambda kv: -abs(kv[1]))[:top_k]
        reasons.append(
            [
                {
                    "feature": base,
                    "impact": round(impact, 4),
                    "direction": "increases" if impact > 0 else "decreases",
                }
                for base, impact in top
            ]
        )
    return reasons


def score_customers(
    pipeline: object, calibrated: CalibratedClassifierCV, orders: pd.DataFrame
) -> pd.DataFrame:
    cutoff = scoring_cutoff(orders)
    feat = build_scoring_features(orders, cutoff)
    proba = calibrated.predict_proba(feat[FEATURE_COLUMNS])[:, 1]
    expected_value = proba * feat["monetary_avg"].to_numpy()

    scores = pd.DataFrame(
        {
            "customer_unique_id": feat["customer_unique_id"].to_numpy(),
            "reactivation_probability": np.round(proba, 6),
            "expected_value": np.round(expected_value, 2),
            "recency_days": feat["recency_days"].to_numpy(),
            "frequency": feat["frequency"].to_numpy(),
            "monetary_total": np.round(feat["monetary_total"].to_numpy(), 2),
        }
    )
    ranked = scores["expected_value"].rank(method="first")
    scores["value_tier"] = pd.qcut(
        ranked, [0, 0.5, 0.8, 1.0], labels=["low", "mid", "high"]
    ).astype(str)
    scores["reasons"] = [json.dumps(r) for r in compute_reasons(pipeline, feat)]
    return scores


def build_and_save() -> dict:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    pipeline = load_model()
    val = pd.read_parquet(PROCESSED_DIR / "val.parquet")
    test = pd.read_parquet(PROCESSED_DIR / "test.parquet")

    calibrated = calibrate(pipeline, val)
    brier_before = brier_score_loss(
        test[LABEL], pipeline.predict_proba(test[FEATURE_COLUMNS])[:, 1]
    )
    brier_after = brier_score_loss(
        test[LABEL], calibrated.predict_proba(test[FEATURE_COLUMNS])[:, 1]
    )

    orders = order_level()
    scores = score_customers(pipeline, calibrated, orders)

    version = json.loads((ARTIFACTS_DIR / "model_card_latest.json").read_text())["version"]
    scored_at = datetime.now(UTC).isoformat()
    scores["model_version"] = version
    scores["scored_at"] = scored_at
    scores.to_parquet(SCORES_PATH, index=False)

    manifest = {
        "model_version": version,
        "scored_at": scored_at,
        "cutoff": scoring_cutoff(orders),
        "customers_scored": int(len(scores)),
        "brier_before_calibration": round(float(brier_before), 5),
        "brier_after_calibration": round(float(brier_after), 5),
        "value_tier_counts": scores["value_tier"].value_counts().to_dict(),
    }
    SCORING_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    print(json.dumps(build_and_save(), indent=2))


if __name__ == "__main__":
    main()
