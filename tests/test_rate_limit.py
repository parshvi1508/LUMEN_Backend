from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from crm_api.rate_limit import RateLimiter, ip_key, make_rate_limit_dependency


def test_allow_within_limit() -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=60.0)
    assert limiter.allow("k", now=0.0)
    assert limiter.allow("k", now=0.0)
    assert not limiter.allow("k", now=0.0)


def test_window_expiry_resets() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=10.0)
    assert limiter.allow("k", now=0.0)
    assert not limiter.allow("k", now=5.0)
    assert limiter.allow("k", now=11.0)


def test_keys_are_isolated() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60.0)
    assert limiter.allow("a", now=0.0)
    assert limiter.allow("b", now=0.0)
    assert not limiter.allow("a", now=0.0)


async def test_dependency_returns_429_over_limit() -> None:
    limiter = RateLimiter(max_requests=1, window_seconds=60.0)
    app = FastAPI()
    dep = make_rate_limit_dependency(limiter, ip_key)

    @app.get("/x", dependencies=[Depends(dep)])
    async def x() -> dict:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        first = await c.get("/x")
        second = await c.get("/x")

    assert first.status_code == 200
    assert second.status_code == 429
