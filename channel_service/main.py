import asyncio
import json
import logging
import random
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI

from channel_service.config import get_settings
from channel_service.schemas import SendAccepted, SendBatch, SendMessage
from channel_service.sender import dead_letters, post_with_retry
from channel_service.simulator import plan_events

logger = logging.getLogger("channel_service")

_rng = random.Random()
_tasks: set[asyncio.Task] = set()

# Persistent delivery plan file so in-flight deliveries survive restarts.
# Written before task starts, removed when task completes.
PENDING_PATH = Path("/tmp/channel_pending.json")


def _load_pending() -> list[dict]:
    if PENDING_PATH.exists():
        try:
            return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_pending(pending: list[dict]) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps(pending, default=str), encoding="utf-8")


def _add_pending(message: SendMessage) -> None:
    pending = _load_pending()
    pending.append(
        {
            "communication_id": str(message.communication_id),
            "channel": message.channel,
            "body": message.body,
            "added_at": datetime.now(UTC).isoformat(),
        }
    )
    _save_pending(pending)


def _remove_pending(communication_id: str) -> None:
    pending = _load_pending()
    pending = [p for p in pending if p["communication_id"] != communication_id]
    _save_pending(pending)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.client = httpx.AsyncClient(timeout=10.0)
    # Resume any pending deliveries from a previous run
    pending = _load_pending()
    if pending:
        logger.info("Resuming %d pending deliveries from previous run", len(pending))
        for item in pending:
            msg = SendMessage(
                communication_id=uuid.UUID(item["communication_id"]),
                channel=item.get("channel", "email"),
                body=item.get("body", ""),
            )
            _spawn_delivery(app.state.client, msg)
    yield
    # Drain: wait for in-flight tasks to finish (with timeout)
    if _tasks:
        logger.info("Draining %d in-flight delivery tasks", len(_tasks))
        done, still_pending = await asyncio.wait(_tasks, timeout=30.0)
        if still_pending:
            logger.warning("%d tasks did not complete before shutdown", len(still_pending))
    await app.state.client.aclose()


app = FastAPI(title="Channel Service", lifespan=lifespan)


async def deliver_message(client: httpx.AsyncClient, message: SendMessage) -> None:
    settings = get_settings()
    comm_id = str(message.communication_id)
    try:
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
                        "communication_id": comm_id,
                        "event_id": event_id,
                        "event_type": planned.event_type,
                        "occurred_at": datetime.now(UTC).isoformat(),
                    }
                ],
            )
    finally:
        _remove_pending(comm_id)


def _spawn_delivery(client: httpx.AsyncClient, message: SendMessage) -> None:
    task = asyncio.create_task(deliver_message(client, message))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@app.post("/send", response_model=SendAccepted, status_code=202)
async def send(batch: SendBatch) -> SendAccepted:
    for message in batch.messages:
        _add_pending(message)
        _spawn_delivery(app.state.client, message)
    return SendAccepted(accepted=len(batch.messages))


@app.get("/dead-letters")
async def get_dead_letters() -> list[dict]:
    return dead_letters


@app.get("/pending")
async def get_pending() -> list[dict]:
    return _load_pending()


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "in_flight": len(_tasks)}
