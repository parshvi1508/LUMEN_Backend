import os

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

load_dotenv()
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("CHANNEL_HMAC_SECRET", "test-hmac-secret")
os.environ.setdefault("CHANNEL_SEND_URL", "http://channel/send")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")


@pytest_asyncio.fixture
async def db_session():
    from crm_api.config import get_settings

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:
        await engine.dispose()
        pytest.skip("database unreachable, DB-backed tests need a live DATABASE_URL")
    trans = await conn.begin()
    session = AsyncSession(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    yield session
    await session.close()
    await trans.rollback()
    await conn.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    from httpx import ASGITransport, AsyncClient

    from crm_api.db import get_session
    from crm_api.main import app

    async def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
