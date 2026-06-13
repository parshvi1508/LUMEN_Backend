import json
import uuid

import httpx
import pytest_asyncio

from crm_api.models import Campaign, Communication, Customer, Segment


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def narrative(text: str) -> str:
    return json.dumps({"narrative": text})


@pytest_asyncio.fixture
async def ai_env(client):
    from crm_api.http_client import get_http_client
    from crm_api.main import app

    scripts: list[str] = []
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return completion(scripts[min(len(requests) - 1, len(scripts) - 1)])

    stub_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_http_client] = lambda: stub_client

    async def configure(*contents: str):
        scripts.extend(contents)

    yield client, configure, requests
    app.dependency_overrides.pop(get_http_client, None)
    await stub_client.aclose()


async def make_campaign(db_session, statuses: list[tuple[str, int]]) -> uuid.UUID:
    segment = Segment(name="seg", definition={"op": "AND", "rules": []}, source="manual")
    db_session.add(segment)
    await db_session.flush()
    campaign = Campaign(
        name="camp",
        segment_id=segment.id,
        channel="email",
        message_template="hi",
        status="active",
        audience_size=len(statuses),
    )
    db_session.add(campaign)
    await db_session.flush()
    for status, rank in statuses:
        customer = Customer(name="C", external_id=f"ins_{uuid.uuid4().hex[:8]}")
        db_session.add(customer)
        await db_session.flush()
        db_session.add(
            Communication(
                campaign_id=campaign.id,
                customer_id=customer.id,
                channel="email",
                rendered_message="hi",
                status=status,
                status_rank=rank,
            )
        )
    await db_session.flush()
    return campaign.id


async def test_happy_path(ai_env, db_session) -> None:
    client, configure, requests = ai_env
    campaign_id = await make_campaign(
        db_session, [("delivered", 20), ("delivered", 20), ("converted", 60)]
    )
    await configure(narrative("Of 3 messages, 1 converted and 2 were delivered."))

    resp = await client.get(f"/api/v1/ai/campaigns/{campaign_id}/insight")
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == str(campaign_id)
    assert body["narrative"]
    labels = {f["label"]: f["value"] for f in body["facts"]}
    assert labels["total"] == 3
    assert labels["converted"] == 1
    assert len(requests) == 1


async def test_grounded_percentage(ai_env, db_session) -> None:
    client, configure, requests = ai_env
    campaign_id = await make_campaign(db_session, [("failed", 15), ("delivered", 20)])
    await configure(narrative("The failure rate was 50% across 2 sends."))

    resp = await client.get(f"/api/v1/ai/campaigns/{campaign_id}/insight")
    assert resp.status_code == 200
    assert len(requests) == 1


async def test_ungrounded_number_drives_repair(ai_env, db_session) -> None:
    client, configure, requests = ai_env
    campaign_id = await make_campaign(db_session, [("delivered", 20), ("converted", 60)])
    await configure(
        narrative("We reached 999 customers this week."),
        narrative("Of 2 messages, 1 converted."),
    )

    resp = await client.get(f"/api/v1/ai/campaigns/{campaign_id}/insight")
    assert resp.status_code == 200
    assert len(requests) == 2


async def test_ungrounded_twice_gives_422(ai_env, db_session) -> None:
    client, configure, requests = ai_env
    campaign_id = await make_campaign(db_session, [("delivered", 20), ("converted", 60)])
    await configure(narrative("We reached 999 customers."))

    resp = await client.get(f"/api/v1/ai/campaigns/{campaign_id}/insight")
    assert resp.status_code == 422
    assert len(requests) == 2


async def test_unknown_campaign_404_no_llm(ai_env) -> None:
    client, _, requests = ai_env
    resp = await client.get("/api/v1/ai/campaigns/00000000-0000-0000-0000-000000000000/insight")
    assert resp.status_code == 404
    assert requests == []


async def test_zero_activity_allows_zero(ai_env, db_session) -> None:
    client, configure, requests = ai_env
    campaign_id = await make_campaign(db_session, [("queued", 0), ("queued", 0)])
    await configure(narrative("Nothing has sent yet, 0 conversions so far."))

    resp = await client.get(f"/api/v1/ai/campaigns/{campaign_id}/insight")
    assert resp.status_code == 200
    assert len(requests) == 1
