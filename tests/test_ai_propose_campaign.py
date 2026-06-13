import json
import uuid

import httpx
import pytest_asyncio


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def variant(label: str) -> dict:
    return {
        "variant": label,
        "message": f"Hi {{{{first_name}}}}, {label} deal.",
        "tone": label,
        "reasoning": f"{label} suits the goal.",
    }


def proposal(city: str, channel: str = "email", field: str = "city") -> str:
    rule = {"field": field, "cmp": "eq", "value": city}
    return json.dumps(
        {
            "segment": {
                "definition": {"op": "AND", "rules": [rule]},
                "rationale": f"Customers in {city}.",
            },
            "recommended_channel": channel,
            "channel_reasoning": f"{channel} reaches them best.",
            "variants": [variant("a"), variant("b"), variant("c")],
        }
    )


@pytest_asyncio.fixture
async def propose_env(client):
    from crm_api.http_client import get_http_client
    from crm_api.main import app

    scripts: list[str] = []
    llm_requests: list[httpx.Request] = []
    channel_batches: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if "groq" in host or "openrouter" in host:
            llm_requests.append(request)
            return completion(scripts[min(len(llm_requests) - 1, len(scripts) - 1)])
        channel_batches.append(json.loads(request.content))
        return httpx.Response(202, json={"accepted": len(json.loads(request.content)["messages"])})

    stub_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_http_client] = lambda: stub_client

    async def configure(*contents: str):
        scripts.extend(contents)

    yield client, configure, llm_requests, channel_batches
    app.dependency_overrides.pop(get_http_client, None)
    await stub_client.aclose()


async def seed_city(client, count: int) -> str:
    city = f"PropCity{uuid.uuid4().hex[:8]}"
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
    return city


async def propose(client, configure, count: int, channel: str = "email") -> dict:
    city = await seed_city(client, count)
    await configure(proposal(city, channel))
    resp = await client.post("/api/v1/ai/propose-campaign", json={"goal": f"reach {city}"})
    assert resp.status_code == 200
    return resp.json()


async def test_propose_happy_path(propose_env) -> None:
    client, configure, llm_requests, _ = propose_env
    body = await propose(client, configure, 4)

    assert body["proposal_state"] == "pending"
    assert body["audience_size"] == 4
    assert body["recommended_channel"] == "email"
    assert len(body["variants"]) == 3
    assert body["channel_reasoning"]
    assert uuid.UUID(body["campaign_id"])
    assert len(llm_requests) == 1


async def test_propose_repair_then_success(propose_env) -> None:
    client, configure, llm_requests, _ = propose_env
    city = await seed_city(client, 2)
    await configure("not json", proposal(city))

    resp = await client.post("/api/v1/ai/propose-campaign", json={"goal": f"reach {city}"})
    assert resp.status_code == 200
    assert len(llm_requests) == 2


async def test_propose_bad_twice_gives_422(propose_env) -> None:
    client, configure, llm_requests, _ = propose_env
    city = await seed_city(client, 1)
    await configure(proposal(city, field="ssn"))

    resp = await client.post("/api/v1/ai/propose-campaign", json={"goal": f"reach {city}"})
    assert resp.status_code == 422
    assert len(llm_requests) == 2


async def test_execute_before_approve_refused(propose_env) -> None:
    client, configure, _, channel_batches = propose_env
    body = await propose(client, configure, 2)

    resp = await client.post(f"/api/v1/campaigns/{body['campaign_id']}/execute")
    assert resp.status_code == 409
    assert channel_batches == []


async def test_approve_then_execute_dispatches(propose_env) -> None:
    client, configure, _, channel_batches = propose_env
    body = await propose(client, configure, 3)
    campaign_id = body["campaign_id"]

    approve = await client.post(f"/api/v1/campaigns/{campaign_id}/approve")
    assert approve.status_code == 200

    execute = await client.post(f"/api/v1/campaigns/{campaign_id}/execute")
    assert execute.status_code == 200
    assert execute.json()["status"] == "active"
    sent = [m for batch in channel_batches for m in batch["messages"]]
    assert len(sent) == 3


async def test_approve_unknown_404_and_execute_plain_campaign_409(propose_env) -> None:
    client, _, _, _ = propose_env
    missing = await client.post("/api/v1/campaigns/00000000-0000-0000-0000-000000000000/approve")
    assert missing.status_code == 404

    city = await seed_city(client, 1)
    definition = {"op": "AND", "rules": [{"field": "city", "cmp": "eq", "value": city}]}
    seg = await client.post(
        "/api/v1/segments",
        json={"name": city, "definition": definition},
    )
    camp = await client.post(
        "/api/v1/campaigns",
        json={
            "name": city,
            "segment_id": seg.json()["id"],
            "channel": "email",
            "message_template": "hi",
        },
    )
    execute = await client.post(f"/api/v1/campaigns/{camp.json()['id']}/execute")
    assert execute.status_code == 409


async def test_empty_goal_422_no_llm(propose_env) -> None:
    client, _, llm_requests, _ = propose_env
    resp = await client.post("/api/v1/ai/propose-campaign", json={"goal": ""})
    assert resp.status_code == 422
    assert llm_requests == []
