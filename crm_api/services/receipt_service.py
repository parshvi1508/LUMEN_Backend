"""Receipt processing. communication_events is append-only, INSERT only.

Never add UPDATE or DELETE against communication_events in this file or anywhere.
At 1000 concurrent users the same process_batch interface swaps its internals
for a Redis or SQS buffer with batched flush, the API shape does not change.
"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from crm_api.models import Communication, CommunicationEvent
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
        for comm_id, ev in best.items():
            rank = STATUS_RANKS[ev.event_type]
            await session.execute(
                update(Communication)
                .where(Communication.id == comm_id, Communication.status_rank < rank)
                .values(status=ev.event_type, status_rank=rank)
            )
            await session.execute(
                update(Communication)
                .where(Communication.id == comm_id)
                .values(
                    last_event_at=func.greatest(
                        func.coalesce(Communication.last_event_at, ev.occurred_at),
                        ev.occurred_at,
                    )
                )
            )

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
