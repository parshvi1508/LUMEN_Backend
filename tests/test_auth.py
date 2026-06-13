import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.test_receipts import event, post_receipts

SECRET = os.environ["SUPABASE_JWT_SECRET"]


def token(secret: str = SECRET, aud: str = "authenticated", expires_in: int = 3600) -> str:
    claims = {
        "sub": str(uuid.uuid4()),
        "email": "user@test.local",
        "aud": aud,
        "exp": datetime.now(UTC) + timedelta(seconds=expires_in),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


@pytest_asyncio.fixture
async def auth_client(db_session):
    from crm_api.db import get_session
    from crm_api.main import app

    async def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def auth_header(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def test_valid_token_passes(auth_client) -> None:
    resp = await auth_client.get("/api/v1/segments", headers=auth_header(token()))
    assert resp.status_code != 401
    assert resp.status_code == 200


async def test_missing_token_401(auth_client) -> None:
    resp = await auth_client.get("/api/v1/segments")
    assert resp.status_code == 401


async def test_bad_signature_401(auth_client) -> None:
    resp = await auth_client.get(
        "/api/v1/segments", headers=auth_header(token(secret="wrong-secret"))
    )
    assert resp.status_code == 401


async def test_expired_token_401(auth_client) -> None:
    resp = await auth_client.get("/api/v1/segments", headers=auth_header(token(expires_in=-10)))
    assert resp.status_code == 401


async def test_wrong_audience_401(auth_client) -> None:
    resp = await auth_client.get("/api/v1/segments", headers=auth_header(token(aud="anon")))
    assert resp.status_code == 401


async def test_receipts_open_without_user_token(auth_client) -> None:
    resp = await post_receipts(auth_client, [event(uuid.uuid4(), "delivered")])
    assert resp.status_code != 401
    assert resp.status_code == 200


async def test_health_open_without_token(auth_client) -> None:
    resp = await auth_client.get("/health")
    assert resp.status_code != 401
    assert resp.status_code in (200, 503)
