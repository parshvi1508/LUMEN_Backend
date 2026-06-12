import asyncio
import random
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI

from channel_service.config import get_settings
from channel_service.schemas import SendAccepted, SendBatch, SendMessage
from channel_service.sender import dead_letters, post_with_retry
from channel_service.simulator import plan_events

_rng = random.Random()
_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.client = httpx.AsyncClient(timeout=10.0)
    yield
    await app.state.client.aclose()


app = FastAPI(title="Channel Service", lifespan=lifespan)


async def deliver_message(client: httpx.AsyncClient, message: SendMessage) -> None:
    settings = get_settings()
    plan = plan_events(
        _rng,
        settings.jitter_min_seconds,
        settings.jitter_max_seconds,
        settings.duplicate_probability,
        settings.reorder_probability,
    )
    event_ids: dict[str, str] = {}
    elapsed = 0.0
    for planned in sorted(plan, key=lambda ev: ev.delay_seconds):
        await asyncio.sleep(max(0.0, planned.delay_seconds - elapsed))
        elapsed = planned.delay_seconds
        event_id = event_ids.setdefault(planned.event_type, str(uuid.uuid4()))
        await post_with_retry(
            client,
            settings.crm_receipts_url,
            settings.channel_hmac_secret,
            [
                {
                    "communication_id": str(message.communication_id),
                    "event_id": event_id,
                    "event_type": planned.event_type,
                    "occurred_at": datetime.now(UTC).isoformat(),
                }
            ],
        )


@app.post("/send", response_model=SendAccepted, status_code=202)
async def send(batch: SendBatch) -> SendAccepted:
    for message in batch.messages:
        task = asyncio.create_task(deliver_message(app.state.client, message))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return SendAccepted(accepted=len(batch.messages))


@app.get("/dead-letters")
async def get_dead_letters() -> list[dict]:
    return dead_letters


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
