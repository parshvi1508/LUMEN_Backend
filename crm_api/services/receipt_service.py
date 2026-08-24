"""Receipt processing. communication_events is append-only, INSERT only.

Never add UPDATE or DELETE against communication_events in this file or anywhere.
At 1000 concurrent users the same process_batch interface swaps its internals
for a Redis or SQS buffer with batched flush, the API shape does not change.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Communication, CommunicationEvent, Order
from crm_api.repositories import ingest_repo
from crm_api.schemas.receipts import (
    ReceiptBatch,
    ReceiptEvent,
    ReceiptEventResult,
    ReceiptResponse,
)

STATUS_RANKS: dict[str, int] = {
    "queued": 0,
    "sent": 10,
    "failed": 15,
    "delivered": 20,
    "opened": 30,
    "read": 40,
    "clicked": 50,
    "converted": 60,
}

SIMULATED_CONVERSION_AMOUNT = Decimal("499.00")


async def _create_attributed_orders(
    session: AsyncSession, converted_events: list[ReceiptEvent]
) -> None:
    rows = (
        await session.execute(
            select(Communication.id, Communication.customer_id, Communication.campaign_id).where(
                Communication.id.in_([ev.communication_id for ev in converted_events])
            )
        )
    ).all()
    by_comm = {comm_id: (customer_id, campaign_id) for comm_id, customer_id, campaign_id in rows}

    stmt = (
        insert(Order)
        .values(
            [
                {
                    "external_id": f"conv_{ev.communication_id}",
                    "customer_id": by_comm[ev.communication_id][0],
                    "amount": SIMULATED_CONVERSION_AMOUNT,
                    "items": [],
                    "ordered_at": ev.occurred_at,
                    "attributed_campaign_id": by_comm[ev.communication_id][1],
                }
                for ev in converted_events
            ]
        )
        .on_conflict_do_nothing(index_elements=[Order.external_id])
        .returning(Order.customer_id, Order.amount, Order.ordered_at)
    )
    inserted = [tuple(row) for row in (await session.execute(stmt)).all()]
    await ingest_repo.apply_order_aggregates(session, inserted)


async def process_batch(session: AsyncSession, batch: ReceiptBatch) -> ReceiptResponse:
    deduped: dict[tuple[uuid.UUID, str], ReceiptEvent] = {}
    for ev in batch.events:
        deduped.setdefault((ev.communication_id, ev.event_type), ev)
    events = list(deduped.values())

    known_ids = set(
        await session.scalars(
            select(Communication.id).where(
                Communication.id.in_({ev.communication_id for ev in events})
            )
        )
    )
    valid = [ev for ev in events if ev.communication_id in known_ids]

    inserted: set[tuple[uuid.UUID, str]] = set()
    if valid:
        stmt = (
            insert(CommunicationEvent)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "communication_id": ev.communication_id,
                        "event_type": ev.event_type,
                        "payload": {"event_id": ev.event_id},
                        "occurred_at": ev.occurred_at,
                    }
                    for ev in valid
                ]
            )
            .on_conflict_do_nothing(
                constraint="uq_communication_events_communication_id_event_type"
            )
            .returning(CommunicationEvent.communication_id, CommunicationEvent.event_type)
        )
        rows = await session.execute(stmt)
        inserted = {tuple(row) for row in rows.all()}

        new_events = [ev for ev in valid if (ev.communication_id, ev.event_type) in inserted]
        best: dict[uuid.UUID, ReceiptEvent] = {}
        for ev in new_events:
            current = best.get(ev.communication_id)
            if current is None or STATUS_RANKS[ev.event_type] > STATUS_RANKS[current.event_type]:
                best[ev.communication_id] = ev
        if best:
            params: dict[str, object] = {}
            value_clauses = []
            for i, (comm_id, ev) in enumerate(best.items()):
                rank = STATUS_RANKS[ev.event_type]
                params[f"id_{i}"] = comm_id
                params[f"status_{i}"] = ev.event_type
                params[f"rank_{i}"] = rank
                params[f"at_{i}"] = ev.occurred_at
                value_clauses.append(
                    f"(:id_{i}::uuid, :status_{i}::text, :rank_{i}::int, :at_{i}::timestamptz)"
                )
            await session.execute(
                text(
                    "UPDATE communications AS c"
                    " SET status = CASE WHEN c.status_rank < v.new_rank"
                    "   THEN v.new_status ELSE c.status END,"
                    " status_rank = CASE WHEN c.status_rank < v.new_rank"
                    "   THEN v.new_rank ELSE c.status_rank END,"
                    " last_event_at = GREATEST("
                    "   COALESCE(c.last_event_at, v.occurred_at), v.occurred_at)"
                    f" FROM (VALUES {', '.join(value_clauses)})"
                    " AS v(id, new_status, new_rank, occurred_at)"
                    " WHERE c.id = v.id"
                ),
                params,
            )

        converted_events = [ev for ev in new_events if ev.event_type == "converted"]
        if converted_events:
            await _create_attributed_orders(session, converted_events)

    await session.commit()

    def result_for(ev: ReceiptEvent) -> str:
        if ev.communication_id not in known_ids:
            return "unknown_communication"
        if (ev.communication_id, ev.event_type) in inserted:
            return "accepted"
        return "duplicate"

    return ReceiptResponse(
        results=[
            ReceiptEventResult(
                communication_id=ev.communication_id,
                event_type=ev.event_type,
                result=result_for(ev),
            )
            for ev in batch.events
        ]
    )
