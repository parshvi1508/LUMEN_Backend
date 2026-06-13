import json
import uuid

import httpx
import pytest_asyncio


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def variant(label: str) -> dict:
    return {
        "variant": label,
        "message": f"Hi {{{{first_name}}}}, {label} offer inside.",
        "tone": label,
        "reasoning": f"{label} fits lapsed buyers.",
    }


def valid_output(n: int = 3) -> str:
    return json.dumps({"variants": [variant(f"v{i}") for i in range(n)]})


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


async def seed_segment(client) -> str:
    resp = await client.post(
        "/api/v1/segments",
        json={
            "name": f"seg_{uuid.uuid4().hex[:8]}",
            "definition": {
                "op": "AND",
                "rules": [{"field": "total_spend", "cmp": "gte", "value": 1000}],
            },
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_happy_path(ai_env) -> None:
    client, configure, requests = ai_env
    segment_id = await seed_segment(client)
    await configure(valid_output(3))

    resp = await client.post(
        "/api/v1/ai/draft-messages",
        json={
            "campaign_intent": "win back lapsed buyers",
            "segment_id": segment_id,
            "channel": "email",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["segment_id"] == segment_id
    assert body["channel"] == "email"
    assert len(body["variants"]) == 3
    for v in body["variants"]:
        assert v["message"] and v["tone"] and v["reasoning"]
    assert len(requests) == 1


async def test_repair_then_success(ai_env) -> None:
    client, configure, requests = ai_env
    segment_id = await seed_segment(client)
    await configure("not json at all", valid_output(3))

    resp = await client.post(
        "/api/v1/ai/draft-messages",
        json={"campaign_intent": "promo", "segment_id": segment_id, "channel": "sms"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["variants"]) == 3
    assert len(requests) == 2


async def test_known_bad_twice_gives_422(ai_env) -> None:
    client, configure, requests = ai_env
    segment_id = await seed_segment(client)
    bad = json.dumps({"variants": [{"variant": "v", "message": "m", "reasoning": "r"}]})
    await configure(bad)

    resp = await client.post(
        "/api/v1/ai/draft-messages",
        json={"campaign_intent": "promo", "segment_id": segment_id, "channel": "whatsapp"},
    )
    assert resp.status_code == 422
    assert len(requests) == 2


async def test_unknown_segment_404_no_llm(ai_env) -> None:
    client, _, requests = ai_env
    resp = await client.post(
        "/api/v1/ai/draft-messages",
        json={
            "campaign_intent": "promo",
            "segment_id": "00000000-0000-0000-0000-000000000000",
            "channel": "email",
        },
    )
    assert resp.status_code == 404
    assert requests == []


async def test_empty_intent_422_no_llm(ai_env) -> None:
    client, _, requests = ai_env
    segment_id = await seed_segment(client)
    resp = await client.post(
        "/api/v1/ai/draft-messages",
        json={"campaign_intent": "", "segment_id": segment_id, "channel": "email"},
    )
    assert resp.status_code == 422
    assert requests == []


async def test_variant_count_tolerance(ai_env) -> None:
    client, configure, requests = ai_env
    segment_id = await seed_segment(client)
    await configure(valid_output(2))

    resp = await client.post(
        "/api/v1/ai/draft-messages",
        json={"campaign_intent": "promo", "segment_id": segment_id, "channel": "email"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["variants"]) == 2
    assert len(requests) == 1
