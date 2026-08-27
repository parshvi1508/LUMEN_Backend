"""Train and register the reactivation-propensity model.

The full pipeline (preprocessor plus classifier) is fit end to end on train only,
so serving loads one artifact and there is no train/serve skew. The dataset is
imbalanced (about 1.2 percent positive), so training uses class weighting and the
model is judged on PR-AUC, ROC-AUC, and Brier calibration, never accuracy. The
decision threshold is chosen by expected profit, not a naive 0.5.
"""

import hashlib
import json
from datetime import UTC, datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline

from ml import wandb_logger
from ml.config import (
    ARTIFACTS_DIR,
    CUTOFF,
    HORIZON_DAYS,
    MANIFEST_PATH,
    PROCESSED_DIR,
)
from ml.features import FEATURE_COLUMNS, LABEL
from ml.split import make_preprocessor

SEED = 42
DEFAULT_COST_PER_CONTACT = 1.0


def build_pipeline(max_iter: int = 400) -> Pipeline:
    model = HistGradientBoostingClassifier(
        class_weight="balanced",
        learning_rate=0.05,
        max_iter=max_iter,
        early_stopping=True,
        validation_fraction=0.15,
        random_state=SEED,
    )
    return Pipeline([("pre", make_preprocessor()), ("clf", model)])


def train_model(train: pd.DataFrame, max_iter: int = 400) -> Pipeline:
    pipeline = build_pipeline(max_iter)
    pipeline.fit(train[FEATURE_COLUMNS], train[LABEL])
    return pipeline


def predict_proba(pipeline: Pipeline, frame: pd.DataFrame) -> np.ndarray:
    return pipeline.predict_proba(frame[FEATURE_COLUMNS])[:, 1]


def evaluate(pipeline: Pipeline, frame: pd.DataFrame) -> dict:
    proba = predict_proba(pipeline, frame)
    y = frame[LABEL].to_numpy()
    return {
        "n": int(len(y)),
        "positive_rate": round(float(y.mean()), 4),
        "pr_auc": round(float(average_precision_score(y, proba)), 4),
        "roc_auc": round(float(roc_auc_score(y, proba)), 4),
        "brier": round(float(brier_score_loss(y, proba)), 5),
    }


def choose_threshold(y_true: np.ndarray, proba: np.ndarray, value: float, cost: float) -> dict:
    """Threshold that maximizes expected profit: contact everyone above it,
    earn ``value`` per true reactivation and pay ``cost`` per contact.
    """
    order = np.argsort(-proba)
    sorted_y = y_true[order]
    sorted_p = proba[order]
    tp = np.cumsum(sorted_y)
    contacts = np.arange(1, len(sorted_y) + 1)
    profit = tp * value - contacts * cost
    best = int(np.argmax(profit))
    return {
        "threshold": round(float(sorted_p[best]), 6),
        "expected_profit": round(float(profit[best]), 2),
        "contacts": int(contacts[best]),
        "true_positives": int(tp[best]),
        "value_per_reactivation": round(float(value), 2),
        "cost_per_contact": round(float(cost), 2),
    }


def _data_hash() -> str:
    if MANIFEST_PATH.exists():
        return hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()[:16]
    return "unknown"


def build_and_save() -> dict:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    train = pd.read_parquet(PROCESSED_DIR / "train.parquet")
    val = pd.read_parquet(PROCESSED_DIR / "val.parquet")
    test = pd.read_parquet(PROCESSED_DIR / "test.parquet")

    wandb_logger.init(
        project="lumen-reactivation",
        name="reactivation-train",
        config={"max_iter": 400, "features": FEATURE_COLUMNS},
    )

    pipeline = train_model(train)

    value = float(train.loc[train[LABEL] == 1, "monetary_avg"].mean())
    threshold = choose_threshold(
        val[LABEL].to_numpy(), predict_proba(pipeline, val), value, DEFAULT_COST_PER_CONTACT
    )

    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    model_path = ARTIFACTS_DIR / f"model_{version}.joblib"
    joblib.dump(pipeline, model_path)
    joblib.dump(pipeline, ARTIFACTS_DIR / "model_latest.joblib")

    card = {
        "version": version,
        "sklearn_pipeline": [step for step, _ in pipeline.steps],
        "cutoff": CUTOFF,
        "horizon_days": HORIZON_DAYS,
        "data_hash": _data_hash(),
        "features": FEATURE_COLUMNS,
        "threshold": threshold,
        "metrics": {
            "val": evaluate(pipeline, val),
            "test": evaluate(pipeline, test),
        },
    }
    (ARTIFACTS_DIR / f"model_card_{version}.json").write_text(
        json.dumps(card, indent=2), encoding="utf-8"
    )
    (ARTIFACTS_DIR / "model_card_latest.json").write_text(
        json.dumps(card, indent=2), encoding="utf-8"
    )

    registry_path = ARTIFACTS_DIR / "registry.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else []
    registry.append({"version": version, "test_pr_auc": card["metrics"]["test"]["pr_auc"]})
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

    wandb_logger.log_metrics(card["metrics"]["test"])
    wandb_logger.log_model_card(card, name="reactivation-card")
    wandb_logger.log_artifact_file(model_path, f"reactivation-model-{version}")
    wandb_logger.finish()

    return card


def main() -> None:
    print(json.dumps(build_and_save(), indent=2))


if __name__ == "__main__":
    main()
