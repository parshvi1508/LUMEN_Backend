import json
import uuid

import httpx
import pytest_asyncio


def completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def valid_output(city: str) -> str:
    return json.dumps(
        {
            "definition": {"op": "AND", "rules": [{"field": "city", "cmp": "eq", "value": city}]},
            "rationale": f"Customers in {city}.",
        }
    )


@pytest_asyncio.fixture
async def ai_env(client):
    from crm_api.http_client import get_http_client
    from crm_api.main import app

    scripts: list[str] = []
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        content = scripts[min(len(requests) - 1, len(scripts) - 1)]
        return completion(content)

    stub_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_http_client] = lambda: stub_client

    async def configure(*contents: str):
        scripts.extend(contents)

    yield client, configure, requests
    app.dependency_overrides.pop(get_http_client, None)
    await stub_client.aclose()


async def seed_city(client, count: int) -> str:
    city = f"AICity{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        "/api/v1/customers/bulk",
        json={
            "customers": [
                {
                    "external_id": f"{city}_{i}",
                    "name": f"Cust {i}",
                    "email": f"c{i}@{city.lower()}.test",
                    "city": city,
                }
                for i in range(count)
            ]
        },
    )
    assert resp.status_code < 300
    return city


async def test_happy_path(ai_env) -> None:
    client, configure, requests = ai_env
    city = await seed_city(client, 5)
    await configure(valid_output(city))

    resp = await client.post("/api/v1/ai/nl-to-segment", json={"prompt": f"people in {city}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["definition"]["rules"][0]["value"] == city
    assert body["rationale"]
    assert body["count"] == 5
    assert body["per_rule_impact"][0]["count"] == 5
    assert body["warnings"] == []
    assert len(requests) == 1


async def test_repair_then_success(ai_env) -> None:
    client, configure, requests = ai_env
    city = await seed_city(client, 3)
    await configure("this is not json", valid_output(city))

    resp = await client.post("/api/v1/ai/nl-to-segment", json={"prompt": f"in {city}"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 3
    assert len(requests) == 2


async def test_whitelist_rejection_drives_repair(ai_env) -> None:
    client, configure, requests = ai_env
    city = await seed_city(client, 2)
    bad = json.dumps(
        {
            "definition": {"op": "AND", "rules": [{"field": "ssn", "cmp": "eq", "value": "x"}]},
            "rationale": "bad field",
        }
    )
    await configure(bad, valid_output(city))

    resp = await client.post("/api/v1/ai/nl-to-segment", json={"prompt": f"in {city}"})
    assert resp.status_code == 200
    assert resp.json()["definition"]["rules"][0]["field"] == "city"
    assert len(requests) == 2


async def test_known_bad_twice_gives_422(ai_env) -> None:
    client, configure, requests = ai_env
    bad = json.dumps(
        {
            "definition": {"op": "AND", "rules": [{"field": "ssn", "cmp": "eq", "value": "x"}]},
            "rationale": "still bad",
        }
    )
    await configure(bad)

    resp = await client.post("/api/v1/ai/nl-to-segment", json={"prompt": "give me everyone"})
    assert resp.status_code == 422
    assert len(requests) == 2


async def test_empty_prompt_422_no_llm(ai_env) -> None:
    client, _, requests = ai_env
    resp = await client.post("/api/v1/ai/nl-to-segment", json={"prompt": ""})
    assert resp.status_code == 422
    assert requests == []


async def test_zero_audience_warning(ai_env) -> None:
    client, configure, _ = ai_env
    empty_city = f"Ghost{uuid.uuid4().hex[:8]}"
    await configure(valid_output(empty_city))

    resp = await client.post("/api/v1/ai/nl-to-segment", json={"prompt": "ghosts"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["per_rule_impact"][0]["count"] == 0
    assert any("matches no customers" in w for w in body["warnings"])
