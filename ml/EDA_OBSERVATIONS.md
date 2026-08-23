# EDA Observations: Olist E-Commerce

Source: `ml/eda.py` run on the raw Olist dataset. Every number here comes from
`ml/eda_stats.json`; figures are in `ml/figures/`. Currency is Brazilian Real (BRL),
the dataset's native currency.

## Dataset shape

- Purchase window: 2016-09-04 to 2018-10-17 (772 days). Delivered-order snapshot: 2018-08-29.
- Delivered orders: 96,478. Total delivered revenue (item price + freight): 15,419,773.75 BRL.
- Unique customers (`customer_unique_id`, the real person key, not the per-order `customer_id`): 93,358.
- Sellers: 3,095. This is the tenant dimension for the multi-tenant reframe.
- See `monthly_order_volume.png` (steep growth through 2017, plateau into 2018) and
  `order_status_distribution.png` (delivered dominates).

## Headline finding: this is a near one-time marketplace

- Repeat rate: 3.0% (2,801 of 93,358 customers ordered more than once). One-time rate: 97.0%.
- Orders-per-customer is overwhelmingly 1 (`orders_per_customer.png`).
- For the 3% who repeat, the inter-purchase gap median is 29 days, mean 78.8, p90 240 days
  (3,120 order pairs, `interpurchase_gap_days.png`).

This single fact drives every modeling decision below. Classic per-customer CLV models
(BG/NBD + Gamma-Gamma) assume repeat transactions; with 97% one-timers, per-customer
transaction-count models are weak and would be dishonest to present as precise.

## Money distribution

- Order value: mean 159.83, p50 105.28, p90 305.92, p99 1052.39. Right-skewed
  (`order_value_distribution.png`, clipped at p99).
- Per-customer total spend (CLV proxy): mean 165.17, p50 107.78, p99 1097.06. Close to the
  order-value distribution precisely because most customers buy once.

## Recency (the churn / reactivation signal)

- Days since last order at snapshot: mean 236.9, p50 218, p90 465 (`recency_days_distribution.png`).
- Long recency tails are expected in a one-time marketplace and are the strongest raw signal
  for a reactivation model.

## Satisfaction and payment

- Review score: mean 4.086. Distribution is bimodal-ish: 57,328 five-star vs 11,424 one-star
  (`review_score_distribution.png`). A one-star is a strong non-repeat predictor to test.
- Payment mix: credit_card 76,795, boleto 19,784, voucher 5,775, debit_card 1,529.
- Installments: mean 2.85, max 24.

## Category and seller concentration

- Top categories by items: bed_bath_table (11,115), health_beauty (9,670), sports_leisure (8,641),
  furniture_decor, computers_accessories (`top_categories.png`).
- Seller revenue concentration (`seller_revenue_concentration.png`): top seller 1.58% of revenue,
  top 10 sellers 12.8%, and 562 sellers (18.2% of all sellers) generate 80% of revenue.
  Moderate concentration, so a per-seller (per-tenant) view is meaningful and not dominated by one seller.

## Implications for the ML layer (S20 features, S21 models)

1. Reframe the target honestly. Primary model = repeat / reactivation propensity: will a customer
   place another order within a horizon H after a cutoff date. Positive class is about 3%, so this
   is an imbalanced binary problem. Evaluate with PR-AUC and calibration, never raw accuracy; use
   class weights, not naive resampling that leaks.
2. CLV = predicted next-order value times P(repeat). Value model trained on the repeat subset;
   probability from the propensity model. Report per-seller aggregate CLV where per-customer is thin.
3. Features (as-of the cutoff only): recency, frequency, monetary, tenure, mean review score,
   payment type, installments, freight-to-price ratio, delivery delay (delivered minus estimated),
   dominant category. All computed strictly from data at or before the cutoff.
4. Leak-safe temporal split: choose cutoff T, build features from orders at or before T, label from
   orders in (T, T+H]. Validation uses a later cutoff with no window overlap. Fit every encoder and
   scaler on train only, then transform validation and test. No customer appears in both train and
   test label windows in a way that leaks the future.
5. Tenant handling: one global model with tenant-aware features, scored per tenant, rather than
   3,095 tiny per-seller models. Keeps latency low and avoids cold-start for small sellers.

## Honest caveats to state in the interview

- 97% one-time buyers means "churn" is really "reactivation"; do not oversell a subscription-style
  churn model.
- Dataset currency is BRL and the window ends 2018; any absolute money figure is historical, not live.
- Review text is unused for now; only the numeric score enters features.
