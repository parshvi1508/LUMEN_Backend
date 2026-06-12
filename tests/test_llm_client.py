import json

import httpx
import pytest

from crm_api.config import get_settings
from crm_api.services.llm_client import LLMUnavailableError, complete

EM_DASH = chr(0x2014)
MESSAGES = [{"role": "user", "content": "hello"}]


def ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def make_client(groq_responder, openrouter_responder):
    calls: dict[str, list[httpx.Request]] = {"groq": [], "openrouter": []}

    def handler(request: httpx.Request) -> httpx.Response:
        provider = "groq" if "groq" in request.url.host else "openrouter"
        calls[provider].append(request)
        responder = groq_responder if provider == "groq" else openrouter_responder
        return responder(len(calls[provider]), request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def sleep_recorder():
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleep, sleeps


async def test_groq_success_no_fallback() -> None:
    client, calls = make_client(lambda n, r: ok("hi there"), lambda n, r: ok("nope"))
    sleep, sleeps = sleep_recorder()

    result = await complete(client, MESSAGES, sleep=sleep)

    assert result.text == "hi there"
    assert result.provider == "groq"
    assert result.model == get_settings().groq_model
    assert len(calls["groq"]) == 1
    assert calls["openrouter"] == []
    assert sleeps == []


async def test_groq_500_storm_falls_back() -> None:
    client, calls = make_client(
        lambda n, r: httpx.Response(500, json={"error": "boom"}),
        lambda n, r: ok("fallback answer"),
    )
    sleep, sleeps = sleep_recorder()

    result = await complete(client, MESSAGES, sleep=sleep)

    assert result.text == "fallback answer"
    assert result.provider == "openrouter"
    assert result.model == get_settings().openrouter_model
    assert len(calls["groq"]) == 4
    assert len(calls["openrouter"]) == 1
    assert sleeps == [1.0, 2.0, 4.0]


async def test_both_providers_exhausted() -> None:
    client, calls = make_client(
        lambda n, r: httpx.Response(503),
        lambda n, r: httpx.Response(500),
    )
    sleep, sleeps = sleep_recorder()

    with pytest.raises(LLMUnavailableError):
        await complete(client, MESSAGES, sleep=sleep)

    assert len(calls["groq"]) == 4
    assert len(calls["openrouter"]) == 4
    assert sleeps == [1.0, 2.0, 4.0, 1.0, 2.0, 4.0]


async def test_non_retryable_401_immediate_fallback() -> None:
    client, calls = make_client(
        lambda n, r: httpx.Response(401),
        lambda n, r: ok("rescued"),
    )
    sleep, sleeps = sleep_recorder()

    result = await complete(client, MESSAGES, sleep=sleep)

    assert result.provider == "openrouter"
    assert len(calls["groq"]) == 1
    assert sleeps == []


async def test_em_dashes_stripped_centrally() -> None:
    content = f"win back{EM_DASH}lapsed buyers {EM_DASH} this weekend"
    client, _ = make_client(lambda n, r: ok(content), lambda n, r: ok(""))
    sleep, _ = sleep_recorder()

    result = await complete(client, MESSAGES, sleep=sleep)

    assert EM_DASH not in result.text
    assert result.text == "win back, lapsed buyers, this weekend"


async def test_request_shape_per_provider() -> None:
    client, calls = make_client(
        lambda n, r: httpx.Response(401),
        lambda n, r: ok("x"),
    )
    sleep, _ = sleep_recorder()
    settings = get_settings()

    await complete(client, MESSAGES, json_mode=True)

    groq_req = calls["groq"][0]
    assert groq_req.headers["Authorization"] == f"Bearer {settings.groq_api_key}"
    groq_body = json.loads(groq_req.content)
    assert groq_body["model"] == settings.groq_model
    assert groq_body["messages"] == MESSAGES
    assert groq_body["response_format"] == {"type": "json_object"}

    or_req = calls["openrouter"][0]
    assert or_req.headers["Authorization"] == f"Bearer {settings.openrouter_api_key}"
    assert json.loads(or_req.content)["model"] == settings.openrouter_model

    plain_client, plain_calls = make_client(lambda n, r: ok("y"), lambda n, r: ok(""))
    await complete(plain_client, MESSAGES, sleep=sleep)
    assert "response_format" not in json.loads(plain_calls["groq"][0].content)
