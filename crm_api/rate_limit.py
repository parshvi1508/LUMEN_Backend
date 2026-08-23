"""In-process fixed-window rate limiting.

Keyed per user for AI routes and per client IP for receipts, protecting the
scarce free LLM quota. Single-instance state is enough at current scale; the
swap at volume is a shared store (Redis) behind the same RateLimiter interface.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._max:
            return False
        hits.append(now)
        return True


def _client_key(request: Request) -> str:
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


def user_key(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user and user.get("sub"):
        return f"user:{user['sub']}"
    return _client_key(request)


def ip_key(request: Request) -> str:
    return _client_key(request)


def make_rate_limit_dependency(
    limiter: RateLimiter, key_func: Callable[[Request], str]
) -> Callable[[Request], object]:
    async def dependency(request: Request) -> None:
        if not limiter.allow(key_func(request)):
            raise HTTPException(status_code=429, detail="rate limit exceeded")

    return dependency
