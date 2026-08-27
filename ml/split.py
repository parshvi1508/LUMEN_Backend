"""Build the modeling dataset: leak-safe temporal features, a stratified
customer-level split, and a preprocessor fit on train only.

Each customer is one row, so a stratified random split keeps every customer in
exactly one split. The preprocessor (impute, scale, one-hot) is fit on train and
only transformed onto validation and test, so no test statistic leaks into fitting.
"""

import json

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ml.config import (
    ARTIFACTS_DIR,
    CUTOFF,
    HORIZON_DAYS,
    MANIFEST_PATH,
    PREPROCESSOR_PATH,
    PROCESSED_DIR,
)
from ml.dataset import order_level
from ml.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    LABEL,
    NUMERIC_FEATURES,
    TEXT_FEATURES,
    build_features,
)

SEED = 42


SVD_COMPONENTS = 5


def make_preprocessor() -> ColumnTransformer:
    numeric = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    text = Pipeline(
        [
            ("tfidf", TfidfVectorizer(max_features=500, stop_words=None, sublinear_tf=True)),
            ("svd", TruncatedSVD(n_components=SVD_COMPONENTS, random_state=42)),
        ]
    )
    return ColumnTransformer(
        [
            ("num", numeric, NUMERIC_FEATURES),
            ("cat", categorical, CATEGORICAL_FEATURES),
            ("txt", text, TEXT_FEATURES[0]),
        ]
    )


def stratified_split(
    feat: pd.DataFrame, seed: int = SEED
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train, temp = train_test_split(feat, test_size=0.30, stratify=feat[LABEL], random_state=seed)
    val, test = train_test_split(temp, test_size=0.50, stratify=temp[LABEL], random_state=seed)
    return train, val, test


def _positive_rate(frame: pd.DataFrame) -> float:
    return round(float(frame[LABEL].mean()), 4)


def build_and_save() -> dict:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    orders = order_level()
    feat = build_features(orders, CUTOFF, HORIZON_DAYS)
    train, val, test = stratified_split(feat)

    preprocessor = make_preprocessor()
    preprocessor.fit(train[FEATURE_COLUMNS])
    joblib.dump(preprocessor, PREPROCESSOR_PATH)

    for name, frame in (("train", train), ("val", val), ("test", test)):
        frame.to_parquet(PROCESSED_DIR / f"{name}.parquet", index=False)

    manifest = {
        "cutoff": CUTOFF,
        "horizon_days": HORIZON_DAYS,
        "seed": SEED,
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "transformed_feature_names": preprocessor.get_feature_names_out().tolist(),
        "counts": {"train": len(train), "val": len(val), "test": len(test)},
        "positive_rate": {
            "train": _positive_rate(train),
            "val": _positive_rate(val),
            "test": _positive_rate(test),
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    manifest = build_and_save()
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
