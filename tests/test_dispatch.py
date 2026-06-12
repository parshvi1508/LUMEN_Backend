import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from crm_api.models import Communication, Customer
from crm_api.services.dispatch_service import resolve_recipient
from tests.test_receipts import event, post_receipts


@pytest.fixture
def channel_stub():
    stub = FastAPI()
    batches: list[dict] = []

    @stub.post("/send", status_code=202)
    async def send(payload: dict) -> dict:
        batches.append(payload)
        return {"accepted": len(payload["messages"])}

    return stub, batches


@pytest_asyncio.fixture
async def dispatch_env(client, channel_stub):
    from crm_api.http_client import get_http_client
    from crm_api.main import app

    stub, batches = channel_stub
    async with AsyncClient(
        transport=ASGITransport(app=stub), base_url="http://channel"
    ) as stub_client:
        app.dependency_overrides[get_http_client] = lambda: stub_client
        yield client, batches
        app.dependency_overrides.pop(get_http_client, None)


async def seed_campaign(client, count: int, channel: str = "email") -> dict:
    city = f"DispatchCity{uuid.uuid4().hex[:8]}"
    if count:
        resp = await client.post(
            "/api/v1/customers/bulk",
            json={
                "customers": [
                    {
                        "external_id": f"{city}_{i}",
                        "name": f"Cust {i}",
                        "email": f"c{i}@{city.lower()}.test",
                        "phone": f"+91{i:010d}",
                        "city": city,
                    }
                    for i in range(count)
                ]
            },
        )
        assert resp.status_code < 300
    seg = await client.post(
        "/api/v1/segments",
        json={
            "name": city,
            "definition": {
                "op": "AND",
                "rules": [{"field": "city", "cmp": "eq", "value": city}],
            },
        },
    )
    assert seg.status_code == 201
    camp = await client.post(
        "/api/v1/campaigns",
        json={
            "name": city,
            "segment_id": seg.json()["id"],
            "channel": channel,
            "message_template": "Hi {{first_name}}",
        },
    )
    assert camp.status_code == 201
    assert camp.json()["audience_size"] == count
    return camp.json()


async def test_dispatch_posts_batches_of_50(dispatch_env) -> None:
    client, batches = dispatch_env
    campaign = await seed_campaign(client, 120)

    resp = await client.post(f"/api/v1/campaigns/{campaign['id']}/dispatch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    assert body["dispatched_at"] is not None

    assert [len(b["messages"]) for b in batches] == [50, 50, 20]
    sent_ids = [m["communication_id"] for b in batches for m in b["messages"]]
    assert len(sent_ids) == 120
    assert len(set(sent_ids)) == 120
    for batch in batches:
        for message in batch["messages"]:
            assert message["channel"] == "email"
            assert message["recipient"]
            assert message["body"].startswith("Hi ")


async def test_dispatch_unknown_campaign_404(dispatch_env) -> None:
    client, _ = dispatch_env
    resp = await client.post("/api/v1/campaigns/00000000-0000-0000-0000-000000000000/dispatch")
    assert resp.status_code == 404


async def test_dispatch_non_draft_409(dispatch_env) -> None:
    client, batches = dispatch_env
    campaign = await seed_campaign(client, 2)

    first = await client.post(f"/api/v1/campaigns/{campaign['id']}/dispatch")
    assert first.status_code == 200
    second = await client.post(f"/api/v1/campaigns/{campaign['id']}/dispatch")
    assert second.status_code == 409
    assert len(batches) == 1


async def test_dispatch_empty_audience(dispatch_env) -> None:
    client, batches = dispatch_env
    campaign = await seed_campaign(client, 0)

    resp = await client.post(f"/api/v1/campaigns/{campaign['id']}/dispatch")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    assert batches == []


async def test_end_to_end_status_progression(dispatch_env, db_session) -> None:
    client, batches = dispatch_env
    campaign = await seed_campaign(client, 2)

    resp = await client.post(f"/api/v1/campaigns/{campaign['id']}/dispatch")
    assert resp.status_code == 200

    comm_id = batches[0]["messages"][0]["communication_id"]

    async def status_of() -> tuple[str, int]:
        row = (
            await db_session.execute(
                select(Communication.status, Communication.status_rank).where(
                    Communication.id == uuid.UUID(comm_id)
                )
            )
        ).one()
        return tuple(row)

    assert await status_of() == ("queued", 0)

    sent = await post_receipts(client, [event(comm_id, "sent")])
    assert sent.status_code == 200
    assert await status_of() == ("sent", 10)

    delivered = await post_receipts(client, [event(comm_id, "delivered")])
    assert delivered.status_code == 200
    assert await status_of() == ("delivered", 20)


def test_resolve_recipient_prefers_channel_field() -> None:
    customer = Customer(name="A", email="a@x.test", phone="+911234567890")
    assert resolve_recipient(customer, "email") == "a@x.test"
    assert resolve_recipient(customer, "sms") == "+911234567890"
    assert resolve_recipient(customer, "whatsapp") == "+911234567890"


def test_resolve_recipient_falls_back() -> None:
    customer = Customer(name="A", email=None, phone="+911234567890")
    assert resolve_recipient(customer, "email") == "+911234567890"
    no_contact = Customer(id=uuid.uuid4(), name="B", email=None, phone=None)
    assert resolve_recipient(no_contact, "sms") == str(no_contact.id)
