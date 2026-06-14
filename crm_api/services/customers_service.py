"""Customer read + CSV upload. Read powers the dashboard and customers table;
upload writes the denormalized spend/order_count directly (CSV-sourced figures),
unlike order ingest which derives them.
"""

import math
import uuid
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Customer
from crm_api.schemas.customers import (
    CustomerOut,
    PaginatedCustomers,
    UploadRequest,
    UploadResult,
)


async def list_customers(
    session: AsyncSession, search: str | None, page: int, page_size: int
) -> PaginatedCustomers:
    page = max(page, 0)
    page_size = min(max(page_size, 1), 500)

    base = select(Customer)
    if search:
        term = f"%{search}%"
        base = base.where(
            or_(
                Customer.name.ilike(term),
                Customer.email.ilike(term),
                Customer.external_id.ilike(term),
            )
        )

    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.scalars(
        base.order_by(Customer.total_spend.desc(), Customer.created_at.desc())
        .offset(page * page_size)
        .limit(page_size)
    )
    data = [CustomerOut.model_validate(c) for c in rows]
    page_count = math.ceil((total or 0) / page_size) if total else 0
    return PaginatedCustomers(
        data=data, total=total or 0, page=page, page_size=page_size, page_count=page_count
    )


async def upload_customers(session: AsyncSession, payload: UploadRequest) -> UploadResult:
    # last row wins on duplicate external_id within the batch
    deduped = {r.external_id: r for r in payload.rows}
    rows = list(deduped.values())
    ext_ids = list(deduped.keys())

    existing = set(
        await session.scalars(
            select(Customer.external_id).where(Customer.external_id.in_(ext_ids))
        )
    )

    values = [
        {
            "id": uuid.uuid4(),
            "external_id": r.external_id,
            "name": r.name,
            "email": r.email,
            "city": r.city,
            "total_spend": r.total_spend if r.total_spend is not None else Decimal("0"),
            "order_count": r.order_count if r.order_count is not None else 0,
        }
        for r in rows
    ]
    stmt = pg_insert(Customer).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Customer.external_id],
        set_={
            "name": stmt.excluded.name,
            "email": stmt.excluded.email,
            "city": stmt.excluded.city,
            "total_spend": stmt.excluded.total_spend,
            "order_count": stmt.excluded.order_count,
        },
    )
    await session.execute(stmt)
    await session.commit()

    created = sum(1 for r in rows if r.external_id not in existing)
    updated = len(rows) - created
    return UploadResult(created=created, updated=updated, rejected=[])
