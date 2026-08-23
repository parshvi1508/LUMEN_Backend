"""Shared paths for the ML pipeline. One definition so EDA, feature building,
training, and scoring never disagree on where data and artifacts live.
"""

from pathlib import Path

ML_DIR = Path(__file__).resolve().parent
RAW_DIR = ML_DIR / "data" / "raw" / "olist" / "archive"
FIGURES_DIR = ML_DIR / "figures"
STATS_PATH = ML_DIR / "eda_stats.json"

CSV_FILES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}
