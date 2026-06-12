import asyncio
import hashlib
import hmac
import json
import logging

import httpx

logger = logging.getLogger("channel_service")

BACKOFF_SECONDS = [1, 4, 16]

dead_letters: list[dict] = []


def sign_body(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    secret: str,
    events: list[dict],
    sleep=asyncio.sleep,
) -> bool:
    body = json.dumps({"events": events}).encode()
    headers = {"Content-Type": "application/json", "X-Signature": sign_body(body, secret)}
    last_error = ""
    for backoff in BACKOFF_SECONDS:
        try:
            resp = await client.post(url, content=body, headers=headers)
            if resp.is_success:
                return True
            last_error = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        await sleep(backoff)
    dead_letters.append({"events": events, "error": last_error})
    logger.error("dead-letter after %d attempts: %s", len(BACKOFF_SECONDS), last_error)
    return False
