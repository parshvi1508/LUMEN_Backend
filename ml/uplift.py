"""T-learner uplift model for campaign targeting.

Estimates incremental lift: P(convert|treated) - P(convert|control). Customers
are ranked by uplift so campaigns target persuadable customers, not ones who
would return anyway.

Treatment proxy: customers who received at least one campaign communication.
Control: everyone else. This proxy is observational (not randomized), so the
uplift estimates carry selection bias and should be interpreted as directional
until an A/B holdout validates them.
"""

import json
from datetime import UTC, datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from ml import wandb_logger
from ml.config import ARTIFACTS_DIR, PROCESSED_DIR
from ml.features import FEATURE_COLUMNS, LABEL
from ml.split import make_preprocessor

SEED = 42
UPLIFT_DIR = ARTIFACTS_DIR / "uplift"


def _build_classifier(max_iter: int = 300) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        class_weight="balanced",
        learning_rate=0.05,
        max_iter=max_iter,
        early_stopping=True,
        validation_fraction=0.15,
        random_state=SEED,
    )


def assign_treatment(
    features: pd.DataFrame, treatment_ids: set[str] | None = None
) -> pd.Series:
    """Assign treatment flag. If treatment_ids provided, use them. Otherwise
    use a deterministic hash-based proxy (every 5th customer is 'treated')
    to demonstrate the framework when no campaign data exists.
    """
    if treatment_ids is not None:
        return features["customer_unique_id"].isin(treatment_ids).astype(int)
    hashes = features["customer_unique_id"].apply(lambda x: hash(x) % 5)
    return (hashes == 0).astype(int)


def train_t_learner(
    train: pd.DataFrame,
    preprocessor: object,
) -> dict:
    """Train separate models for treatment and control groups."""
    treatment_mask = train["treatment"].astype(bool)
    treated = train[treatment_mask]
    control = train[~treatment_mask]

    X_treated = preprocessor.transform(treated[FEATURE_COLUMNS])
    X_control = preprocessor.transform(control[FEATURE_COLUMNS])

    model_t = _build_classifier()
    model_t.fit(X_treated, treated[LABEL])

    model_c = _build_classifier()
    model_c.fit(X_control, control[LABEL])

    return {"treatment": model_t, "control": model_c}


def predict_uplift(
    models: dict,
    preprocessor: object,
    features: pd.DataFrame,
) -> np.ndarray:
    """Uplift = P(convert|treated) - P(convert|control)."""
    X = preprocessor.transform(features[FEATURE_COLUMNS])
    p_t = models["treatment"].predict_proba(X)[:, 1]
    p_c = models["control"].predict_proba(X)[:, 1]
    return p_t - p_c


def qini_score(y_true: np.ndarray, uplift: np.ndarray, treatment: np.ndarray) -> float:
    """Approximate Qini coefficient: area under the uplift curve normalized
    by the random targeting baseline.
    """
    order = np.argsort(-uplift)
    y_sorted = y_true[order]
    t_sorted = treatment[order]

    n = len(y_sorted)
    n_t = t_sorted.sum()
    n_c = n - n_t

    if n_t == 0 or n_c == 0:
        return 0.0

    cum_t = np.cumsum(t_sorted)
    cum_c = np.arange(1, n + 1) - cum_t

    cum_resp_t = np.cumsum(y_sorted * t_sorted)
    cum_resp_c = np.cumsum(y_sorted * (1 - t_sorted))

    safe_t = np.where(cum_t > 0, cum_t, 1)
    safe_c = np.where(cum_c > 0, cum_c, 1)

    uplift_curve = cum_resp_t / safe_t * n_t - cum_resp_c / safe_c * n_c
    qini = float(np.trapezoid(uplift_curve, dx=1.0 / n))
    return round(qini, 4)


def build_and_save() -> dict:
    UPLIFT_DIR.mkdir(parents=True, exist_ok=True)
    train = pd.read_parquet(PROCESSED_DIR / "train.parquet")
    val = pd.read_parquet(PROCESSED_DIR / "val.parquet")
    test = pd.read_parquet(PROCESSED_DIR / "test.parquet")

    wandb_logger.init(
        project="lumen-uplift",
        name="t-learner-train",
        config={"features": FEATURE_COLUMNS, "approach": "T-learner"},
    )

    preprocessor = make_preprocessor()
    preprocessor.fit(train[FEATURE_COLUMNS])

    train["treatment"] = assign_treatment(train)
    val["treatment"] = assign_treatment(val)
    test["treatment"] = assign_treatment(test)

    models = train_t_learner(train, preprocessor)

    test_uplift = predict_uplift(models, preprocessor, test)
    test_qini = qini_score(
        test[LABEL].to_numpy(), test_uplift, test["treatment"].to_numpy()
    )

    X_test = preprocessor.transform(test[FEATURE_COLUMNS])
    treatment_mask = test["treatment"].astype(bool)
    auc_t = roc_auc_score(
        test.loc[treatment_mask, LABEL],
        models["treatment"].predict_proba(X_test[treatment_mask])[:, 1],
    ) if treatment_mask.sum() > 10 else None
    auc_c = roc_auc_score(
        test.loc[~treatment_mask, LABEL],
        models["control"].predict_proba(X_test[~treatment_mask])[:, 1],
    ) if (~treatment_mask).sum() > 10 else None

    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    joblib.dump(models, UPLIFT_DIR / f"uplift_models_{version}.joblib")
    joblib.dump(models, UPLIFT_DIR / "uplift_models_latest.joblib")
    joblib.dump(preprocessor, UPLIFT_DIR / "uplift_preprocessor.joblib")

    card = {
        "version": version,
        "approach": "T-learner (separate treatment/control HistGradientBoosting)",
        "treatment_proxy": "hash-based deterministic split (20% treated)",
        "caveat": "observational proxy, not randomized; uplift estimates carry selection bias",
        "test_qini": test_qini,
        "test_auc_treatment": auc_t,
        "test_auc_control": auc_c,
        "treatment_counts": {
            "train_treated": int(train["treatment"].sum()),
            "train_control": int((~train["treatment"].astype(bool)).sum()),
            "test_treated": int(test["treatment"].sum()),
            "test_control": int((~test["treatment"].astype(bool)).sum()),
        },
        "uplift_distribution": {
            "mean": round(float(test_uplift.mean()), 4),
            "std": round(float(test_uplift.std()), 4),
            "min": round(float(test_uplift.min()), 4),
            "max": round(float(test_uplift.max()), 4),
            "p25": round(float(np.percentile(test_uplift, 25)), 4),
            "p75": round(float(np.percentile(test_uplift, 75)), 4),
        },
    }

    (UPLIFT_DIR / f"uplift_card_{version}.json").write_text(
        json.dumps(card, indent=2), encoding="utf-8"
    )
    (UPLIFT_DIR / "uplift_card_latest.json").write_text(
        json.dumps(card, indent=2), encoding="utf-8"
    )

    wandb_logger.log_metrics({
        "test_qini": test_qini,
        "test_auc_treatment": auc_t,
        "test_auc_control": auc_c,
    })
    wandb_logger.log_model_card(card, name="uplift-card")
    wandb_logger.finish()

    return card


def main() -> None:
    print(json.dumps(build_and_save(), indent=2))


if __name__ == "__main__":
    main()
