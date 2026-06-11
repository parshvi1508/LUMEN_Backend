import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from crm_api import db
from crm_api.config import Settings
from crm_api.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def fake_ping() -> bool:
        return True

    monkeypatch.setattr(db, "ping", fake_ping)
    with TestClient(app) as c:
        yield c


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": True}


def test_healthz_alias(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["db"] is True


def test_health_db_down(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def flaky_ping() -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        raise ConnectionError("db unreachable")

    monkeypatch.setattr(db, "ping", flaky_ping)
    with TestClient(app) as c:
        resp = c.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "db": False}


def test_missing_database_url_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_non_postgres_url_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="mysql://u:p@h/db")
