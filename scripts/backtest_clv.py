"""Backtest predicted expected_value against realized 90-day revenue.

Reads customer_scores and orders from the database, computes:
  - Per customer: predicted EV vs sum(order.amount) in [scored_at, scored_at + 90d]
  - Aggregate: MAE, RMSE, correlation, decile calibration table

Usage:
  python -m scripts.backtest_clv [--tenant-id UUID] [--horizon-days 90]
"""

import argparse
import asyncio
import uuid
from datetime import timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from crm_api.config import get_settings
from crm_api.models import CustomerScore, Order


async def run_backtest(
    tenant_id: uuid.UUID | None, horizon_days: int
) -> None:
    engine = create_async_engine(get_settings().database_url, echo=False)
    async with AsyncSession(engine) as session:
        score_q = select(
            CustomerScore.customer_id,
            CustomerScore.expected_value,
            CustomerScore.scored_at,
        )
        if tenant_id is not None:
            score_q = score_q.where(CustomerScore.tenant_id == tenant_id)

        score_rows = (await session.execute(score_q)).all()
        if not score_rows:
            print("No scores found. Nothing to backtest.")
            return

        customer_ids = [r.customer_id for r in score_rows]
        order_q = select(
            Order.customer_id, Order.amount, Order.ordered_at
        ).where(Order.customer_id.in_(customer_ids))
        order_rows = (await session.execute(order_q)).all()

    orders_by_cust: dict[uuid.UUID, list[tuple]] = {}
    for o in order_rows:
        orders_by_cust.setdefault(o.customer_id, []).append(
            (o.ordered_at, float(o.amount))
        )

    predicted = []
    realized = []
    for s in score_rows:
        window_start = s.scored_at
        window_end = window_start + timedelta(days=horizon_days)
        rev = sum(
            amt
            for ts, amt in orders_by_cust.get(s.customer_id, [])
            if window_start <= ts <= window_end
        )
        predicted.append(float(s.expected_value))
        realized.append(rev)

    pred = np.array(predicted)
    real = np.array(realized)
    errors = pred - real

    mae = float(np.mean(np.abs(errors)))
    rmse = float(np.sqrt(np.mean(errors ** 2)))
    corr = float(np.corrcoef(pred, real)[0, 1]) if len(pred) > 1 else 0.0

    print(f"Customers scored:  {len(pred)}")
    print(f"Horizon:           {horizon_days} days")
    print(f"MAE:               {mae:.2f}")
    print(f"RMSE:              {rmse:.2f}")
    print(f"Correlation:       {corr:.4f}")
    print(f"Mean predicted:    {pred.mean():.2f}")
    print(f"Mean realized:     {real.mean():.2f}")
    print()

    n_deciles = min(10, len(pred))
    if n_deciles < 2:
        return
    indices = np.argsort(pred)
    splits = np.array_split(indices, n_deciles)
    print(f"{'Decile':>7} {'Avg Predicted':>14} {'Avg Realized':>13} {'Count':>6}")
    print("-" * 44)
    for i, chunk in enumerate(splits, 1):
        avg_p = pred[chunk].mean()
        avg_r = real[chunk].mean()
        print(f"{i:>7} {avg_p:>14.2f} {avg_r:>13.2f} {len(chunk):>6}")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest CLV expected_value")
    parser.add_argument("--tenant-id", type=uuid.UUID, default=None)
    parser.add_argument("--horizon-days", type=int, default=90)
    args = parser.parse_args()
    asyncio.run(run_backtest(args.tenant_id, args.horizon_days))


if __name__ == "__main__":
    main()
