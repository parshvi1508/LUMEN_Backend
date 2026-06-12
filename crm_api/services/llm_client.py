"""The single LLM gateway. Groq primary, OpenRouter fallback.

No provider may be called from anywhere else in the codebase. Em dashes are
stripped from model output here, once, centrally.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from crm_api.config import get_settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (1.0, 2.0, 4.0)


class LLMError(Exception):
    pass


class LLMUnavailableError(LLMError):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str


def _strip_em_dashes(text: str) -> str:
    return text.replace(" \u2014 ", ", ").replace("\u2014", ", ")


def _retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


async def complete(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 1024,
    json_mode: bool = False,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> LLMResult:
    settings = get_settings()
    providers = (
        ("groq", GROQ_URL, settings.groq_api_key, settings.groq_model),
        ("openrouter", OPENROUTER_URL, settings.openrouter_api_key, settings.openrouter_model),
    )
    failures: dict[str, str] = {}

    for name, url, api_key, model in providers:
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {api_key}"}

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                failures[name] = repr(exc)
            else:
                if response.status_code == 200:
                    text = response.json()["choices"][0]["message"]["content"]
                    return LLMResult(text=_strip_em_dashes(text), provider=name, model=model)
                failures[name] = f"status {response.status_code}"
                if not _retryable(response.status_code):
                    break
            if attempt < MAX_ATTEMPTS - 1:
                await sleep(BACKOFF_SECONDS[attempt])

    raise LLMUnavailableError(f"all providers failed: {failures}")
