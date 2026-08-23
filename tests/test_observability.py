import json
import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from crm_api.logging_config import JsonFormatter, request_id_var
from crm_api.middleware import REQUEST_ID_HEADER, RequestContextMiddleware


@pytest.fixture
def obs_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    return app


async def test_request_id_generated_when_absent(obs_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=obs_app), base_url="http://test") as c:
        resp = await c.get("/ping")
    assert resp.headers.get(REQUEST_ID_HEADER)


async def test_request_id_preserved_when_provided(obs_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=obs_app), base_url="http://test") as c:
        resp = await c.get("/ping", headers={REQUEST_ID_HEADER: "fixed-id-123"})
    assert resp.headers.get(REQUEST_ID_HEADER) == "fixed-id-123"


def test_json_formatter_emits_valid_json_with_context() -> None:
    token = request_id_var.set("abc")
    try:
        record = logging.LogRecord(
            name="crm_api.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="request",
            args=(),
            exc_info=None,
        )
        record.context = {"status": 200, "path": "/ping"}
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert payload["request_id"] == "abc"
    assert payload["message"] == "request"
    assert payload["status"] == 200
    assert payload["path"] == "/ping"
