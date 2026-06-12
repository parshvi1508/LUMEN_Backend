import hashlib
import hmac
import json
import random
import uuid
from pathlib import Path

import httpx
import pytest

from channel_service import sender
from channel_service.sender import post_with_retry, sign_body
from channel_service.simulator import plan_events

FUNNEL = ["sent", "delivered", "opened", "read", "clicked", "converted"]


def plan(rng, dup=0.0, reorder=0.0):
    return plan_events(rng, 0.0, 1.0, dup, reorder)


def test_outcome_distribution() -> None:
    rng = random.Random(7)
    outcomes = {"delivered": 0, "failed": 0, "stuck": 0}
    for _ in range(5000):
        types = [ev.event_type for ev in plan(rng)]
        if "failed" in types:
            outcomes["failed"] += 1
        elif "delivered" in types:
            outcomes["delivered"] += 1
        else:
            outcomes["stuck"] += 1
    assert 0.88 <= outcomes["delivered"] / 5000 <= 0.92
    assert 0.06 <= outcomes["failed"] / 5000 <= 0.10
    assert 0.01 <= outcomes["stuck"] / 5000 <= 0.03


def test_event_ordering_semantics() -> None:
    rng = random.Random(11)
    for _ in range(2000):
        types = [ev.event_type for ev in plan(rng)]
        unique = list(dict.fromkeys(types))
        assert unique[0] == "sent"
        if "failed" in unique:
            assert unique == ["sent", "failed"]
        else:
            assert unique == FUNNEL[: len(unique)]


def test_duplicates_when_forced() -> None:
    rng = random.Random(3)
    events = plan(rng, dup=1.0)
    types = [ev.event_type for ev in events]
    assert len(types) == 2 * len(set(types))


def test_reorder_when_forced() -> None:
    rng = random.Random(5)
    saw_reorder = False
    for _ in range(200):
        events = plan(rng, reorder=1.0)
        if len(events) > 1:
            by_delay = sorted(events, key=lambda ev: ev.delay_seconds)
            funnel_positions = [FUNNEL.index(ev.event_type) for ev in by_delay if not_failed(ev)]
            if funnel_positions != sorted(funnel_positions):
                saw_reorder = True
                break
    assert saw_reorder


def not_failed(ev) -> bool:
    return ev.event_type != "failed"


async def test_retry_storm_then_dead_letter(monkeypatch) -> None:
    sender.dead_letters.clear()
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await post_with_retry(
        client, "http://crm/api/v1/receipts", "s3cret", [{"x": 1}], sleep=fake_sleep
    )
    assert ok is False
    assert attempts["n"] == 3
    assert sleeps == [1, 4, 16]
    assert len(sender.dead_letters) == 1
    assert sender.dead_letters[0]["error"] == "HTTP 500"
    await client.aclose()


async def test_success_path_signature() -> None:
    sender.dead_letters.clear()
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    events = [
        {
            "communication_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "event_type": "sent",
            "occurred_at": "2026-06-12T10:00:00+00:00",
        }
    ]
    ok = await post_with_retry(client, "http://crm/api/v1/receipts", "s3cret", events)
    assert ok is True
    assert len(captured) == 1
    request = captured[0]
    expected = hmac.new(b"s3cret", request.content, hashlib.sha256).hexdigest()
    assert request.headers["X-Signature"] == expected
    assert json.loads(request.content) == {"events": events}
    assert sign_body(request.content, "s3cret") == expected
    assert sender.dead_letters == []
    await client.aclose()


def test_isolation_no_crm_api_import() -> None:
    src = Path(__file__).parent.parent / "channel_service"
    for f in src.glob("*.py"):
        assert "crm_api" not in f.read_text(encoding="utf-8"), f.name


async def test_send_end_to_end_no_network(monkeypatch) -> None:
    monkeypatch.setenv("JITTER_MIN_SECONDS", "0")
    monkeypatch.setenv("JITTER_MAX_SECONDS", "0")
    monkeypatch.setenv("DUPLICATE_PROBABILITY", "0")
    monkeypatch.setenv("REORDER_PROBABILITY", "0")

    from channel_service import config, main

    config.get_settings.cache_clear()
    received: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    import asyncio

    from httpx import ASGITransport, AsyncClient

    main.app.state.client = AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://ch") as api:
        resp = await api.post(
            "/send",
            json={
                "messages": [
                    {
                        "communication_id": str(uuid.uuid4()),
                        "recipient": "a@b.c",
                        "channel": "email",
                        "body": "hi",
                    }
                ]
            },
        )
        assert resp.status_code == 202
        assert resp.json() == {"accepted": 1}
        for _ in range(100):
            if not main._tasks:
                break
            await asyncio.sleep(0.05)

    assert received
    first_types = [e["events"][0]["event_type"] for e in received]
    assert first_types[0] == "sent"
    config.get_settings.cache_clear()
    await main.app.state.client.aclose()


@pytest.fixture(autouse=True)
def _hmac_env(monkeypatch) -> None:
    monkeypatch.setenv("CHANNEL_HMAC_SECRET", "s3cret")
