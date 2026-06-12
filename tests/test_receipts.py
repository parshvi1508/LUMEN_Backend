import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import func, select

from crm_api.models import Campaign, Communication, CommunicationEvent, Customer, Segment

SECRET = os.environ["CHANNEL_HMAC_SECRET"]


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def event(communication_id, event_type, occurred_at=None):
    return {
        "communication_id": str(communication_id),
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
    }


async def post_receipts(client, events, secret: str = SECRET, omit_signature: bool = False):
    body = json.dumps({"events": events}).encode()
    headers = {"Content-Type": "application/json"}
    if not omit_signature:
        headers["X-Signature"] = sign(body, secret)
    return await client.post("/api/v1/receipts", content=body, headers=headers)


@pytest_asyncio.fixture
async def comm(db_session) -> Communication:
    customer = Customer(name="Receipt Tester", external_id=f"rcpt_{uuid.uuid4().hex[:8]}")
    segment = Segment(name="rcpt seg", definition={"op": "AND", "rules": []}, source="manual")
    db_session.add_all([customer, segment])
    await db_session.flush()
    campaign = Campaign(
        name="rcpt camp",
        segment_id=segment.id,
        channel="email",
        message_template="hi",
        status="draft",
        audience_size=1,
    )
    db_session.add(campaign)
    await db_session.flush()
    communication = Communication(
        campaign_id=campaign.id,
        customer_id=customer.id,
        channel="email",
        rendered_message="hi",
        status="queued",
        status_rank=0,
    )
    db_session.add(communication)
    await db_session.commit()
    return communication


async def get_comm_state(db_session, comm_id):
    row = await db_session.execute(
        select(Communication.status, Communication.status_rank).where(Communication.id == comm_id)
    )
    return row.one()


async def event_count(db_session, comm_id) -> int:
    return await db_session.scalar(
        select(func.count(CommunicationEvent.id)).where(
            CommunicationEvent.communication_id == comm_id
        )
    )


async def test_duplicate_event_single_row(client, db_session, comm) -> None:
    first = await post_receipts(client, [event(comm.id, "sent")])
    assert first.status_code == 200
    assert first.json()["results"][0]["result"] == "accepted"
    state_after_first = await get_comm_state(db_session, comm.id)

    second = await post_receipts(client, [event(comm.id, "sent")])
    assert second.status_code == 200
    assert second.json()["results"][0]["result"] == "duplicate"

    assert await event_count(db_session, comm.id) == 1
    assert await get_comm_state(db_session, comm.id) == state_after_first
    assert state_after_first == ("sent", 10)


async def test_out_of_order_never_downgrades(client, db_session, comm) -> None:
    resp = await post_receipts(client, [event(comm.id, "clicked")])
    assert resp.status_code == 200
    assert await get_comm_state(db_session, comm.id) == ("clicked", 50)

    late = await post_receipts(client, [event(comm.id, "delivered")])
    assert late.status_code == 200
    assert late.json()["results"][0]["result"] == "accepted"

    assert await get_comm_state(db_session, comm.id) == ("clicked", 50)
    logged_types = set(
        await db_session.scalars(
            select(CommunicationEvent.event_type).where(
                CommunicationEvent.communication_id == comm.id
            )
        )
    )
    assert logged_types == {"clicked", "delivered"}


async def test_retry_storm_stable(client, db_session, comm) -> None:
    batch = [
        event(comm.id, "sent"),
        event(comm.id, "delivered"),
        event(comm.id, "opened"),
    ]
    for _ in range(5):
        resp = await post_receipts(client, batch)
        assert resp.status_code == 200

    assert await event_count(db_session, comm.id) == 3
    assert await get_comm_state(db_session, comm.id) == ("opened", 30)


async def test_unknown_communication_id_rejected_cleanly(client, db_session) -> None:
    ghost = uuid.uuid4()
    resp = await post_receipts(client, [event(ghost, "sent")])
    assert resp.status_code == 200
    assert resp.json()["results"][0]["result"] == "unknown_communication"
    assert await event_count(db_session, ghost) == 0


async def test_bad_hmac_rejected_nothing_written(client, db_session, comm) -> None:
    wrong = await post_receipts(client, [event(comm.id, "sent")], secret="wrong-secret")
    assert wrong.status_code == 401

    missing = await post_receipts(client, [event(comm.id, "sent")], omit_signature=True)
    assert missing.status_code == 401

    assert await event_count(db_session, comm.id) == 0
    assert await get_comm_state(db_session, comm.id) == ("queued", 0)


async def test_mixed_batch_per_event_results(client, db_session, comm) -> None:
    await post_receipts(client, [event(comm.id, "sent")])
    ghost = uuid.uuid4()
    resp = await post_receipts(
        client,
        [event(comm.id, "delivered"), event(comm.id, "sent"), event(ghost, "opened")],
    )
    assert resp.status_code == 200
    results = [r["result"] for r in resp.json()["results"]]
    assert results == ["accepted", "duplicate", "unknown_communication"]
    assert await get_comm_state(db_session, comm.id) == ("delivered", 20)


async def test_converted_first_then_sent_stays_converted(client, db_session, comm) -> None:
    await post_receipts(client, [event(comm.id, "converted")])
    assert await get_comm_state(db_session, comm.id) == ("converted", 60)

    await post_receipts(client, [event(comm.id, "sent")])
    assert await get_comm_state(db_session, comm.id) == ("converted", 60)
