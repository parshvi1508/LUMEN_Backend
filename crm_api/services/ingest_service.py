from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.repositories import ingest_repo
from crm_api.schemas.ingest import (
    BulkCustomersRequest,
    BulkOrdersRequest,
    IngestError,
    IngestResult,
)


async def ingest_customers(session: AsyncSession, payload: BulkCustomersRequest) -> IngestResult:
    deduped = {c.external_id: c for c in payload.customers}
    inserted, updated = await ingest_repo.upsert_customers(session, list(deduped.values()))
    await session.commit()
    return IngestResult(inserted=inserted, updated=updated)


async def ingest_orders(session: AsyncSession, payload: BulkOrdersRequest) -> IngestResult:
    deduped = {o.external_id: o for o in payload.orders}
    orders = list(deduped.values())

    customer_ids = await ingest_repo.resolve_customer_ids(
        session, [o.customer_external_id for o in orders]
    )
    errors = [
        IngestError(external_id=o.external_id, reason="unknown customer_external_id")
        for o in orders
        if o.customer_external_id not in customer_ids
    ]
    valid = [o for o in orders if o.customer_external_id in customer_ids]

    inserted_rows = []
    if valid:
        inserted_rows = await ingest_repo.insert_orders(session, valid, customer_ids)
        await ingest_repo.apply_order_aggregates(session, inserted_rows)
    await session.commit()

    return IngestResult(
        inserted=len(inserted_rows),
        skipped_duplicates=len(valid) - len(inserted_rows),
        errors=errors,
    )
